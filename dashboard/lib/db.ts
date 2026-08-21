/**
 * db.ts -- the read-only Postgres pool.
 *
 * "Read-only" is enforced twice, deliberately, because one layer of enforcement is a convention and
 * two is a guarantee:
 *
 *   1. `default_transaction_read_only=on` is set on every connection in this pool. A stray INSERT
 *      is rejected by the SERVER, not by a reviewer noticing it.
 *   2. Only `query()` is exported. There is no transaction helper, no `execute()`, nothing that
 *      would give a route handler a way to write.
 *
 * The dashboard's job is to explain what the analyser decided. Writes belong to the analyser, which
 * has its own credentials and its own concurrency lock.
 *
 * The module-level singleton matters in dev: Next.js hot-reloads modules on every edit, and a pool
 * created per reload leaks connections until Postgres refuses new ones.
 */
import pg, { Pool, type QueryResultRow } from 'pg';

import { env } from './env';

// ------------------------------------------------------------------------------------------------
// Return DATE columns as plain strings.
//
// A SQL DATE has no time and no timezone. `pg` nonetheless parses OID 1082 into a JS Date at LOCAL
// midnight, so on a UTC+5:30 host `2026-08-01` becomes `2026-07-31T18:30:00.000Z` -- and any
// subsequent `toISOString().slice(0, 10)` reports the wrong DAY.
//
// That matters specifically for `savings_reports.as_of`: it is the date the node prices were quoted,
// printed next to a currency figure as its provenance. Displaying it off by one undermines the one
// number in this product that has to be defensible.
// ------------------------------------------------------------------------------------------------
pg.types.setTypeParser(1082, (value: string) => value);
import { CircuitBreaker } from './resilience';
import { UpstreamError } from './problem';

declare global {
  // eslint-disable-next-line no-var
  var __ktPool: Pool | undefined;
  // eslint-disable-next-line no-var
  var __ktDbBreaker: CircuitBreaker | undefined;
}

function createPool(): Pool {
  return new Pool({
    connectionString: env.DATABASE_URL,
    // Small. Next.js route handlers are short-lived readers, and every connection is memory on the
    // database side that the analyser also needs.
    max: 10,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: env.DB_TIMEOUT_MS,
    // Server-side statement timeout. A client-side timeout abandons the wait but leaves the query
    // running, still holding a connection and still burning database CPU.
    statement_timeout: env.DB_TIMEOUT_MS,
    query_timeout: env.DB_TIMEOUT_MS,
    options: '-c default_transaction_read_only=on',
    application_name: 'kubethrifty-dashboard',
  });
}

const pool = globalThis.__ktPool ?? createPool();
const breaker = globalThis.__ktDbBreaker ?? new CircuitBreaker('TimescaleDB', 5, 30_000);

if (env.NODE_ENV !== 'production') {
  globalThis.__ktPool = pool;
  globalThis.__ktDbBreaker = breaker;
}

// An idle-client error (server restart, network reset) is emitted on the pool, not on any awaited
// promise. Without this listener Node treats it as an unhandled 'error' event and kills the process.
pool.on('error', (err) => {
  console.error('[db] idle client error', err);
});

/**
 * Run a parameterised read.
 *
 * `params` is not optional cosmetics: every value reaching SQL here originates in a URL query
 * string. Template-literal interpolation into `sql` would be an injection, so the signature makes
 * the parameterised form the only convenient one.
 */
export async function query<T extends QueryResultRow>(sql: string, params: unknown[] = []): Promise<T[]> {
  return breaker.run(async () => {
    try {
      const result = await pool.query<T>(sql, params);
      return result.rows;
    } catch (err) {
      // Wrapped so the route handler returns 502 (a dependency failed) rather than 500 (we are
      // broken). The original error is preserved for the server log, never for the response body:
      // a pg error message can contain the connection string.
      throw new UpstreamError('TimescaleDB', err instanceof Error ? err.message : 'query failed');
    }
  });
}

/** Health probe. Cheap, and deliberately not routed through the breaker -- its job is to report. */
export async function dbHealthy(): Promise<boolean> {
  try {
    await pool.query('SELECT 1');
    return true;
  } catch {
    return false;
  }
}

export const dbBreakerStatus = () => breaker.status;
