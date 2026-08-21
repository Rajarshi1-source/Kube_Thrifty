/**
 * env.ts -- validated server-side configuration.
 *
 * Parsed ONCE at module load and thrown on if invalid. A dashboard that boots with a malformed
 * DATABASE_URL and only discovers it on the first request has turned a startup failure into an
 * intermittent 500, which is strictly worse: the deploy looked green.
 *
 * Note what is absent: there is no KUBECONFIG, no service-account token, no cluster API URL. The
 * dashboard holds NO cluster credentials, by design. It cannot resize a pod because it has no
 * mechanism to try. Its single write in the entire system is an XADD onto `analysis-jobs`.
 */
import { z } from 'zod';

const schema = z.object({
  DATABASE_URL: z.string().url(),
  REDIS_URL: z.string().url(),
  PROMETHEUS_URL: z.string().url(),

  CLUSTER_NAME: z.string().default('kubethrifty-demo'),

  // Upper bounds on what a client can ask Prometheus for, via our proxy. Clamps, not suggestions.
  PROM_TIMEOUT_MS: z.coerce.number().int().positive().max(30_000).default(8_000),
  PROM_MAX_WINDOW_DAYS: z.coerce.number().int().positive().max(90).default(30),
  PROM_MIN_STEP_SECONDS: z.coerce.number().int().positive().default(30),

  DB_TIMEOUT_MS: z.coerce.number().int().positive().default(5_000),

  // Cache is an optimisation, never load-bearing. Turning it off must leave the app correct.
  CACHE_ENABLED: z
    .string()
    .default('true')
    .transform((v) => v !== 'false'),

  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
});

const parsed = schema.safeParse(process.env);

if (!parsed.success) {
  // Field names only. Printing the values would put DATABASE_URL's password in the pod log.
  const fields = parsed.error.issues.map((i) => i.path.join('.')).join(', ');
  throw new Error(`invalid server configuration; check these variables: ${fields}`);
}

export const env = parsed.data;
export type Env = typeof env;
