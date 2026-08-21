-- ================================================================================================
-- 004_savings.sql -- the bin-packer's output.
--
-- This table exists so the ONE monetary figure in the product has a durable, auditable home. Its
-- shape enforces the rule: money is derived from `nodes_before - nodes_after`, and the price is
-- stamped with the date and region it came from. There is no per-pod currency column anywhere,
-- deliberately -- clouds bill per node, so a per-pod rupee figure would be a fabrication.
-- ================================================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS savings_reports (
    id              BIGSERIAL PRIMARY KEY,
    cluster_id      BIGINT      NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    run_id          TEXT        REFERENCES analysis_runs(run_id) ON DELETE SET NULL,

    scenario        TEXT        NOT NULL
                    CHECK (scenario IN ('fixed_node_pool', 'self_managed_karpenter', 'eks_auto_mode')),

    -- The bin-packing result. The delta between these two is the ONLY thing that earns a currency
    -- symbol.
    nodes_before    INTEGER     NOT NULL CHECK (nodes_before >= 0),
    nodes_after     INTEGER     NOT NULL CHECK (nodes_after  >= 0),
    instance_type   TEXT        NOT NULL,

    -- NULL when the scenario cannot express a realised saving. Under Karpenter, freed capacity may
    -- consolidate rather than produce a billable reduction, and NULL says "not a claim we can make"
    -- where 0 would wrongly say "we checked, there is nothing".
    monthly_saving  NUMERIC(14, 2),
    hourly_price    NUMERIC(10, 6) NOT NULL,
    currency        TEXT        NOT NULL DEFAULT 'USD',

    -- Provenance for the price. A savings figure without these is unfalsifiable: nobody can check
    -- whether it used current prices for the right region.
    as_of           DATE        NOT NULL,
    region          TEXT        NOT NULL,

    -- How the packing was reached: instance sizes, DaemonSet overhead, kube-reserved, max_pods.
    -- Kept so a disputed node count can be re-derived instead of re-argued.
    packing_detail  JSONB       NOT NULL DEFAULT '{}'::JSONB,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A node count cannot legitimately grow from right-sizing. If it does, the run mis-modelled
    -- something (most likely an HPA that will scale out when requests shrink), and the correct
    -- response is a refused write, not a negative saving.
    CONSTRAINT nodes_after_never_exceeds_before CHECK (nodes_after <= nodes_before)
);

CREATE INDEX IF NOT EXISTS idx_savings_cluster_scenario
    ON savings_reports (cluster_id, scenario, created_at DESC);

COMMIT;
