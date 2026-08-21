-- ================================================================================================
-- 001_core.sql -- clusters, analysis runs, recommendations, and the metric hypertable.
--
-- Requires the `-all` TimescaleDB image: the hourly_pod_stats continuous aggregate uses
-- percentile_agg / approx_percentile from the Timescale Toolkit. The plain timescaledb image
-- starts happily and then fails this file with "function percentile_agg does not exist", which is
-- a confusing way to discover an image tag is wrong. See docker-compose.yml.
-- ================================================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;

-- ------------------------------------------------------------------------------------------------
-- clusters
-- ------------------------------------------------------------------------------------------------
CREATE TABLE clusters (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT        NOT NULL UNIQUE,
    provider        TEXT        NOT NULL DEFAULT 'unknown',
    region          TEXT,
    -- Which pricing scenario applies. Determines whether a node delta is a realised saving or
    -- merely unlocked capacity, so it belongs with the cluster, not with the recommendation.
    pricing_scenario TEXT       NOT NULL DEFAULT 'fixed_node_pool',
    kubernetes_version TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------------------------------------
-- analysis_runs -- one row per invocation of the analyser.
-- ------------------------------------------------------------------------------------------------
CREATE TYPE run_status AS ENUM ('running', 'succeeded', 'failed', 'degraded');

CREATE TABLE analysis_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT        NOT NULL UNIQUE,
    cluster_id      BIGINT      NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    status          run_status  NOT NULL DEFAULT 'running',
    window_spec     TEXT        NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    workloads_seen  INTEGER     NOT NULL DEFAULT 0,
    recommendations_made INTEGER NOT NULL DEFAULT 0,
    pr_url          TEXT,
    -- 'degraded' is a first-class outcome, not a failure: it records that the run completed but a
    -- signal was missing, so its recommendations carry a lower evidence tier.
    degraded_reason TEXT,
    error           TEXT
);

CREATE INDEX idx_analysis_runs_cluster_started
    ON analysis_runs (cluster_id, started_at DESC);

-- ------------------------------------------------------------------------------------------------
-- recommendations
--
-- Columns exist for the ARGUMENT, not just the number. `binding_constraint`, `sizing_basis` and
-- `evidence_tier` are what let the dashboard and the PR body explain themselves; without them a
-- recommendation is an unexplained assertion.
-- ------------------------------------------------------------------------------------------------
CREATE TYPE rec_action     AS ENUM ('reduce', 'increase', 'keep', 'needs_review');
CREATE TYPE rec_confidence AS ENUM ('high', 'medium', 'low');

CREATE TABLE recommendations (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              TEXT        NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    cluster_id          BIGINT      NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,

    namespace           TEXT        NOT NULL,
    workload            TEXT        NOT NULL,
    workload_kind       TEXT        NOT NULL DEFAULT 'Deployment',
    container           TEXT        NOT NULL,
    resource            TEXT        NOT NULL CHECK (resource IN ('cpu', 'memory')),

    -- NULL means NOT OBSERVED. Never 0. A CHECK cannot enforce intent, so this is stated here and
    -- honoured by every writer and reader.
    current_request     DOUBLE PRECISION,
    current_limit       DOUBLE PRECISION,
    recommended_request DOUBLE PRECISION NOT NULL,
    recommended_limit   DOUBLE PRECISION NOT NULL,

    action              rec_action     NOT NULL,
    confidence          rec_confidence NOT NULL,

    -- Observed statistics, kept so a recommendation can be re-explained months later without
    -- re-querying a Prometheus whose retention has long since rolled over.
    observed_p50        DOUBLE PRECISION,
    observed_p95        DOUBLE PRECISION,
    observed_p99        DOUBLE PRECISION,
    observed_peak       DOUBLE PRECISION,
    throttle_ratio      DOUBLE PRECISION,
    psi_stalled_ratio   DOUBLE PRECISION,
    oom_events          INTEGER,
    samples             INTEGER     NOT NULL DEFAULT 0,

    forecast_model      TEXT,
    rationale           TEXT        NOT NULL DEFAULT '',
    reduction_blocked   BOOLEAN     NOT NULL DEFAULT FALSE,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_recommendations_run       ON recommendations (run_id);
CREATE INDEX idx_recommendations_workload  ON recommendations (cluster_id, namespace, workload, container);
CREATE INDEX idx_recommendations_action    ON recommendations (action) WHERE action IN ('reduce', 'increase');

-- ------------------------------------------------------------------------------------------------
-- metric_snapshots -- the raw time series. A HYPERTABLE, because this is the table that grows
-- without bound and every query against it is time-ranged.
-- ------------------------------------------------------------------------------------------------
CREATE TABLE metric_snapshots (
    time            TIMESTAMPTZ NOT NULL,
    cluster_id      BIGINT      NOT NULL,
    namespace       TEXT        NOT NULL,
    pod             TEXT        NOT NULL,
    container       TEXT        NOT NULL,

    cpu_cores       DOUBLE PRECISION,
    memory_bytes    DOUBLE PRECISION,
    -- Ratios, stored as ratios. Raw period counters are deliberately NOT stored: keeping them
    -- invites somebody to compare them across windows of different length, which is meaningless.
    throttle_ratio  DOUBLE PRECISION,
    psi_cpu_full    DOUBLE PRECISION,
    psi_mem_full    DOUBLE PRECISION
);

-- 1 day chunks: queries are "last 7d" / "last 14d", so a day-sized chunk lets the planner exclude
-- almost everything. Smaller chunks would multiply planning overhead for no gain.
--
-- `by_range()` is the current dimension-builder API. The older
-- `create_hypertable(rel, 'time', chunk_time_interval => ...)` positional form is deprecated and
-- warns on every run against the pinned 2.29 image.
SELECT create_hypertable(
    'metric_snapshots',
    by_range('time', INTERVAL '1 day'),
    if_not_exists => TRUE
);

CREATE INDEX idx_metric_snapshots_container
    ON metric_snapshots (cluster_id, namespace, pod, container, time DESC);

-- ------------------------------------------------------------------------------------------------
-- hourly_pod_stats -- continuous aggregate.
--
-- MIN_DATA_POINTS is 168 HOURLY samples, so hourly is the natural grain: the sizer reads this view
-- rather than re-aggregating raw 15s samples on every run.
--
-- percentile_agg stores a sketch, so `approx_percentile` can answer ANY percentile later. Storing
-- pre-computed p95/p99 columns instead would mean a schema migration the first time somebody wants
-- p90 -- and percentiles are not re-aggregatable across buckets, so you cannot derive a weekly p95
-- from seven daily p95 values. The sketch is re-aggregatable; the number is not.
--
-- max(memory_bytes) is kept as a plain MAX, deliberately: memory is sized off the PEAK, and a
-- percentile sketch is exactly the wrong tool for a high-water mark.
-- ------------------------------------------------------------------------------------------------
CREATE MATERIALIZED VIEW hourly_pod_stats
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 hour', time) AS bucket,
    cluster_id,
    namespace,
    pod,
    container,
    percentile_agg(cpu_cores)             AS cpu_percentiles,
    max(cpu_cores)                        AS cpu_peak,
    avg(cpu_cores)                        AS cpu_mean,
    -- THE memory sizing basis.
    max(memory_bytes)                     AS memory_peak,
    avg(memory_bytes)                     AS memory_mean,
    percentile_agg(memory_bytes)          AS memory_percentiles,
    max(throttle_ratio)                   AS throttle_ratio_max,
    avg(throttle_ratio)                   AS throttle_ratio_mean,
    max(psi_cpu_full)                     AS psi_cpu_full_max,
    max(psi_mem_full)                     AS psi_mem_full_max,
    count(*)                              AS samples
FROM metric_snapshots
GROUP BY bucket, cluster_id, namespace, pod, container
WITH NO DATA;

-- Refresh trailing data only. The most recent hour is still filling, and refreshing an incomplete
-- bucket would publish a peak that is only a partial peak.
SELECT add_continuous_aggregate_policy(
    'hourly_pod_stats',
    start_offset      => INTERVAL '3 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists     => TRUE
);

-- ------------------------------------------------------------------------------------------------
-- Compression + retention.
--
-- Compress after 7 days: the sizer's default window is 7d, so recent data stays uncompressed and
-- fast, while older chunks (kept for the forecaster's 14-day MIN_POINTS and for post-merge
-- verification) get 10-20x compression.
-- ------------------------------------------------------------------------------------------------
ALTER TABLE metric_snapshots SET (
    timescaledb.compress = TRUE,
    -- Segment by the identity columns so a single container's history decompresses as one unit.
    timescaledb.compress_segmentby = 'cluster_id, namespace, pod, container',
    timescaledb.compress_orderby   = 'time DESC'
);

SELECT add_compression_policy('metric_snapshots', INTERVAL '7 days', if_not_exists => TRUE);

-- 30 days of raw samples. The hourly aggregate outlives it, so a recommendation stays explainable
-- after its raw evidence has aged out.
SELECT add_retention_policy('metric_snapshots', INTERVAL '30 days', if_not_exists => TRUE);

-- Seed the demo cluster so a fresh stack has somewhere to write.
INSERT INTO clusters (name, provider, region, pricing_scenario, kubernetes_version)
VALUES ('kubethrifty-demo', 'aws', 'ap-south-1', 'fixed_node_pool', '1.36')
ON CONFLICT (name) DO NOTHING;

COMMIT;
