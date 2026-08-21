-- Schema smoke test. Asserts the things a silent migration failure would leave subtly broken:
-- the Toolkit is present, both hypertables exist, the continuous aggregate answers percentile
-- queries through rollup(), and the safety CHECK constraints actually refuse bad rows.
\set ON_ERROR_STOP on

\echo '=== extensions ==='
SELECT extname, extversion FROM pg_extension WHERE extname LIKE 'timescale%' ORDER BY extname;

\echo '=== hypertables ==='
SELECT hypertable_name, num_dimensions, num_chunks
  FROM timescaledb_information.hypertables ORDER BY hypertable_name;

\echo '=== continuous aggregates ==='
SELECT view_name, materialized_only, compression_enabled
  FROM timescaledb_information.continuous_aggregates ORDER BY view_name;

\echo '=== policies (compression / retention / refresh) ==='
SELECT proc_name, hypertable_name, schedule_interval
  FROM timescaledb_information.jobs
 WHERE proc_name IS NOT NULL ORDER BY hypertable_name, proc_name;

\echo '=== enums ==='
SELECT t.typname, string_agg(e.enumlabel, ', ' ORDER BY e.enumsortorder) AS labels
  FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
 WHERE t.typname IN ('run_status','rec_action','rec_confidence','rehearsal_outcome')
 GROUP BY t.typname ORDER BY t.typname;

\echo '=== recommendations Rev 3 columns ==='
SELECT column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_name = 'recommendations'
   AND column_name IN ('binding_constraint','sizing_basis','evidence_tier','rehearsal_id','hpa_coupled')
 ORDER BY column_name;

-- ------------------------------------------------------------------------------------------------
-- Toolkit round trip. This is the assertion the `-all` image tag exists for: rollup() merges the
-- hourly percentile sketches and approx_percentile() reads a percentile from the merged result.
-- Averaging hourly p95 values instead would be silently wrong, because percentiles do not compose.
-- ------------------------------------------------------------------------------------------------
\echo '=== toolkit round trip ==='
INSERT INTO metric_snapshots (time, cluster_id, namespace, pod, container,
                              cpu_cores, memory_bytes, throttle_ratio, psi_cpu_full, psi_mem_full)
SELECT now() - (g || ' minutes')::INTERVAL, 1, 'demo', 'demo-pod-abc', 'app',
       0.1 + (g % 100) * 0.004,          -- a spread wide enough for percentiles to differ
       (200 + (g % 50)) * 1024 * 1024,
       CASE WHEN g % 10 = 0 THEN 0.02 ELSE 0.0 END,
       NULL,                              -- PSI not observed: NULL, never 0
       NULL
  FROM generate_series(1, 600) g;

CALL refresh_continuous_aggregate('hourly_pod_stats', NULL, NULL);

SELECT approx_percentile(0.50, rollup(cpu_percentiles))::numeric(10,4) AS p50,
       approx_percentile(0.95, rollup(cpu_percentiles))::numeric(10,4) AS p95,
       approx_percentile(0.99, rollup(cpu_percentiles))::numeric(10,4) AS p99,
       max(cpu_peak)::numeric(10,4)                                    AS cpu_peak,
       pg_size_pretty(max(memory_peak)::bigint)                        AS memory_peak,
       sum(samples)                                                    AS samples
  FROM hourly_pod_stats
 WHERE cluster_id = 1 AND namespace = 'demo' AND container = 'app';

\echo '=== NULL stays NULL (not observed is not zero) ==='
SELECT count(*) AS rows_total,
       count(psi_cpu_full) AS psi_non_null,
       bool_and(psi_cpu_full IS NULL) AS all_psi_null
  FROM metric_snapshots WHERE cluster_id = 1;

-- ------------------------------------------------------------------------------------------------
-- Safety constraints. Each of these SHOULD fail; the test is that the database refuses them.
-- ------------------------------------------------------------------------------------------------
\echo '=== CHECK: rehearsed tier without a rehearsal row must be refused ==='
DO $$
BEGIN
    INSERT INTO analysis_runs (run_id, cluster_id, window_spec) VALUES ('t-1', 1, '7d');
    INSERT INTO recommendations (
        run_id, cluster_id, namespace, workload, container, resource,
        recommended_request, recommended_limit, action, confidence, evidence_tier
    ) VALUES ('t-1', 1, 'demo', 'w', 'app', 'cpu', 0.1, 0.2, 'reduce', 'high', 'rehearsed');
    RAISE EXCEPTION 'FAIL: a rehearsed recommendation was accepted with no rehearsal_id';
EXCEPTION
    WHEN check_violation THEN RAISE NOTICE 'PASS: refused rehearsed tier with no rehearsal_id';
END $$;

\echo '=== CHECK: rehearsed_floor is only allowed on a safe outcome ==='
DO $$
BEGIN
    INSERT INTO rehearsals (
        rehearsal_id, cluster_id, namespace, workload, pod, pod_uid, container,
        original_requests, original_limits, candidate_requests, candidate_limits,
        revert_deadline, outcome, rehearsed_floor
    ) VALUES (
        't-reh-1', 1, 'demo', 'w', 'p', 'uid', 'app',
        '{}'::JSONB, '{}'::JSONB, '{}'::JSONB, '{}'::JSONB,
        now() + INTERVAL '1 hour', 'inconclusive', 0.25
    );
    RAISE EXCEPTION 'FAIL: an inconclusive rehearsal was allowed to publish a floor';
EXCEPTION
    WHEN check_violation THEN RAISE NOTICE 'PASS: refused a floor on a non-safe outcome';
END $$;

\echo '=== CHECK: evidence_tier is constrained to the three known tiers ==='
DO $$
BEGIN
    INSERT INTO recommendations (
        run_id, cluster_id, namespace, workload, container, resource,
        recommended_request, recommended_limit, action, confidence, evidence_tier
    ) VALUES ('t-1', 1, 'demo', 'w2', 'app', 'cpu', 0.1, 0.2, 'reduce', 'high', 'verified');
    RAISE EXCEPTION 'FAIL: an unknown evidence tier was accepted';
EXCEPTION
    WHEN check_violation THEN RAISE NOTICE 'PASS: refused an unknown evidence tier';
END $$;

\echo '=== CHECK: psi_samples.source is constrained ==='
DO $$
BEGIN
    INSERT INTO psi_samples (time, cluster_id, namespace, pod, container, source)
    VALUES (now(), 1, 'demo', 'p', 'app', 'vibes');
    RAISE EXCEPTION 'FAIL: an unknown PSI source was accepted';
EXCEPTION
    WHEN check_violation THEN RAISE NOTICE 'PASS: refused an unknown PSI source';
END $$;

\echo '=== cleanup ==='
DELETE FROM analysis_runs WHERE run_id = 't-1';
DELETE FROM metric_snapshots WHERE cluster_id = 1 AND namespace = 'demo';
CALL refresh_continuous_aggregate('hourly_pod_stats', NULL, NULL);

\echo 'SCHEMA_VERIFY_OK'
