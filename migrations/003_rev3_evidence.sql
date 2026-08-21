-- ================================================================================================
-- 003_rev3_evidence.sql -- the Rev 3 evidence layer.
--
-- Everything here exists so the product can prove what it claims:
--   psi_samples   the pressure and cgroup truth the sizer reasons about
--   rehearsals    what was actually tried on a live pod, and the compensation needed to undo it
--   verdicts      ThriftDetective's output, content-addressed and replayable
--   recommendations gains the columns that record WHICH evidence backed each number
--
-- (002 is intentionally unused: it was reserved during planning and never needed. Numbering is
-- kept stable rather than renumbered, because these files run in filename order on a live volume.)
-- ================================================================================================

BEGIN;

-- ------------------------------------------------------------------------------------------------
-- psi_samples -- pressure and cgroup facts, at higher resolution than metric_snapshots.
--
-- Separate from metric_snapshots because it has a different lifecycle: PSI and memory.peak are the
-- evidence behind a specific decision, so they are retained longer than raw usage and are written
-- by a different producer (the cgroup-truth DaemonSet, not the Prometheus scraper).
-- ------------------------------------------------------------------------------------------------
CREATE TABLE psi_samples (
    time                TIMESTAMPTZ NOT NULL,
    cluster_id          BIGINT      NOT NULL,
    namespace           TEXT        NOT NULL,
    pod                 TEXT        NOT NULL,
    container           TEXT        NOT NULL,

    -- PSI. `full` = every task stalled; `some` = at least one. The gates use `full`, because
    -- `some` is normal on any busy node and would block every reduction.
    psi_cpu_some        DOUBLE PRECISION,
    psi_cpu_full        DOUBLE PRECISION,
    psi_mem_some        DOUBLE PRECISION,
    psi_mem_full        DOUBLE PRECISION,
    psi_io_full         DOUBLE PRECISION,

    -- cgroup v2 truth. memory_peak is THE memory sizing basis: the kernel's own high-water mark,
    -- which cannot miss a spike between scrapes the way a sampled gauge can.
    memory_peak         BIGINT,
    memory_current      BIGINT,
    -- NULL means the cgroup reported "max", i.e. unlimited. It does NOT mean zero. Writing 0 here
    -- would make every peak/limit ratio infinite or undefined.
    memory_max          BIGINT,
    oom_kill_total      BIGINT,

    -- Stored so a ratio can be recomputed later, but always CONSUMED as a ratio.
    throttled_periods   BIGINT,
    cfs_periods         BIGINT,

    -- 'cgroup' (the DaemonSet, authoritative) | 'cadvisor' (scraped, weaker) | 'unavailable'.
    -- Drives the evidence tier: a sample whose source is 'unavailable' can never support
    -- anything better than `partial`.
    source              TEXT        NOT NULL DEFAULT 'cgroup'
                        CHECK (source IN ('cgroup', 'cadvisor', 'unavailable'))
);

SELECT create_hypertable(
    'psi_samples',
    by_range('time', INTERVAL '1 day'),
    if_not_exists => TRUE
);

CREATE INDEX idx_psi_samples_container
    ON psi_samples (cluster_id, namespace, pod, container, time DESC);

ALTER TABLE psi_samples SET (
    timescaledb.compress = TRUE,
    timescaledb.compress_segmentby = 'cluster_id, namespace, pod, container',
    timescaledb.compress_orderby   = 'time DESC'
);
SELECT add_compression_policy('psi_samples', INTERVAL '7 days', if_not_exists => TRUE);
-- 90 days, longer than metric_snapshots' 30: this is the evidence behind a merged change, and it
-- has to outlive the change long enough to defend it.
SELECT add_retention_policy('psi_samples', INTERVAL '90 days', if_not_exists => TRUE);

-- ------------------------------------------------------------------------------------------------
-- rehearsals -- the compensation log for live-pod experiments.
--
-- CRITICAL: a row here is written BEFORE the cluster is touched, carrying original_requests,
-- original_limits and revert_deadline. If the analyser is killed mid-rehearsal, this row is the
-- only thing that knows how to put the pod back -- and the watchdog CronJob acts on it without
-- needing the original process. Writing it afterwards would leave a resized pod with no record.
-- ------------------------------------------------------------------------------------------------
CREATE TYPE rehearsal_outcome AS ENUM (
    'running',
    'safe',                    -- observed, nothing regressed, reverted
    'regressed',               -- a trip condition fired; reverted
    'inconclusive',            -- Infeasible, timed out, or the pod was rescheduled
    'reverted_by_watchdog',    -- the owning process died; the deadline sweeper cleaned up
    'failed'                   -- the experiment itself errored
);

CREATE TABLE rehearsals (
    id                  BIGSERIAL PRIMARY KEY,
    rehearsal_id        TEXT        NOT NULL UNIQUE,
    run_id              TEXT        REFERENCES analysis_runs(run_id) ON DELETE SET NULL,
    cluster_id          BIGINT      NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,

    namespace           TEXT        NOT NULL,
    workload            TEXT        NOT NULL,
    pod                 TEXT        NOT NULL,
    -- The pod UID, captured at the start. If it changes, the pod was rescheduled and the
    -- experiment is measuring a different container: outcome is `inconclusive`, and NOTHING is
    -- reverted -- reverting would patch a pod we never modified.
    pod_uid             TEXT        NOT NULL,
    container           TEXT        NOT NULL,

    -- Compensation. Persisted before the first PATCH.
    original_requests   JSONB       NOT NULL,
    original_limits     JSONB       NOT NULL,
    candidate_requests  JSONB       NOT NULL,
    candidate_limits    JSONB       NOT NULL,

    -- now() + observe + resize_timeout + 600s of slack. The watchdog reverts anything past this.
    revert_deadline     TIMESTAMPTZ NOT NULL,
    reverted_at         TIMESTAMPTZ,

    outcome             rehearsal_outcome NOT NULL DEFAULT 'running',
    -- The floor a `safe` outcome contributes to sizing. NULL for every other outcome: an
    -- inconclusive rehearsal must never become a floor.
    rehearsed_floor     DOUBLE PRECISION,

    -- The evidence, as evaluated by verification.signals.evaluate().
    baseline_signals    JSONB,
    observed_signals    JSONB,
    trip_reasons        TEXT[],

    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,

    CONSTRAINT rehearsed_floor_only_when_safe
        CHECK (rehearsed_floor IS NULL OR outcome = 'safe')
);

-- The watchdog's query: everything still running and past its deadline.
CREATE INDEX idx_rehearsals_deadline
    ON rehearsals (revert_deadline)
    WHERE outcome = 'running';

CREATE INDEX idx_rehearsals_workload
    ON rehearsals (cluster_id, namespace, workload, container, started_at DESC);

-- Concurrency cap enforcement (default 3 per cluster) reads this.
CREATE INDEX idx_rehearsals_running
    ON rehearsals (cluster_id)
    WHERE outcome = 'running';

-- ------------------------------------------------------------------------------------------------
-- verdicts -- ThriftDetective output.
--
-- bundle_sha is the primary identity: sha256 of the evidence bundle's CANONICAL JSON
-- (sort_keys=True, separators=(",",":")). Canonicalisation is what makes the hash stable across
-- machines and Python versions. `thriftctl replay <sha>` reproduces the verdict offline.
-- ------------------------------------------------------------------------------------------------
CREATE TABLE verdicts (
    id                  BIGSERIAL PRIMARY KEY,
    bundle_sha          TEXT        NOT NULL,
    cluster_id          BIGINT      NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,

    namespace           TEXT        NOT NULL,
    workload            TEXT        NOT NULL,
    container           TEXT        NOT NULL,

    rule_id             TEXT        NOT NULL,
    confidence          DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    summary             TEXT        NOT NULL,
    remediation         TEXT        NOT NULL DEFAULT '',
    evidence            JSONB       NOT NULL,

    -- The differentiator: did WE cause this? No external investigator can answer that, because
    -- none of them owns KubeThrifty's change log.
    attributed_change   JSONB,

    -- Pinned so a verdict can be re-read against the ruleset that produced it. A confidence value
    -- is only meaningful next to the ruleset version it was calibrated under.
    ruleset_version     TEXT        NOT NULL,
    -- The determinism anchor CI asserts against.
    verdicts_digest     TEXT        NOT NULL,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The same bundle re-investigated under the same ruleset must not create a second row: the
    -- function is pure, so the verdict is identical and a duplicate would be noise.
    UNIQUE (bundle_sha, rule_id, ruleset_version)
);

CREATE INDEX idx_verdicts_bundle   ON verdicts (bundle_sha);
CREATE INDEX idx_verdicts_workload ON verdicts (cluster_id, namespace, workload, created_at DESC);
CREATE INDEX idx_verdicts_attributed
    ON verdicts ((attributed_change -> 'pr'))
    WHERE attributed_change IS NOT NULL;

-- ------------------------------------------------------------------------------------------------
-- recommendations: the Rev 3 evidence columns.
--
-- These five are what turn a number into a reviewable claim.
-- ------------------------------------------------------------------------------------------------
ALTER TABLE recommendations
    -- Which floor won the max(): p95_margin | peak_margin | forecast | rehearsed | absolute_min.
    ADD COLUMN binding_constraint TEXT,

    -- What the number was derived FROM. For memory this distinguishes the kernel high-water mark
    -- from a sampled gauge, which is the difference between solid and merely plausible.
    ADD COLUMN sizing_basis      TEXT,

    -- rehearsed > modelled > partial. Never overstated: a modelled recommendation is never
    -- described as verified.
    ADD COLUMN evidence_tier     TEXT NOT NULL DEFAULT 'modelled'
                                 CHECK (evidence_tier IN ('rehearsed', 'modelled', 'partial')),

    ADD COLUMN rehearsal_id      TEXT REFERENCES rehearsals(rehearsal_id) ON DELETE SET NULL,

    -- TRUE when an HPA targets this workload's utilisation-of-request. The resource change and the
    -- HPA target change must then ship in ONE PR; the intermediate state is the outage.
    ADD COLUMN hpa_coupled       BOOLEAN NOT NULL DEFAULT FALSE;

-- A `rehearsed` tier without a rehearsal row is a lie the schema can catch.
ALTER TABLE recommendations
    ADD CONSTRAINT rehearsed_tier_requires_rehearsal
    CHECK (evidence_tier <> 'rehearsed' OR rehearsal_id IS NOT NULL);

CREATE INDEX idx_recommendations_evidence ON recommendations (evidence_tier);
CREATE INDEX idx_recommendations_coupled  ON recommendations (hpa_coupled) WHERE hpa_coupled;

COMMIT;
