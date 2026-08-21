/**
 * client.ts -- Valkey cache-aside, and the one write this API is permitted to make.
 *
 * Two rules, both load-bearing:
 *
 *   1. THE CACHE IS NEVER LOAD-BEARING. Every read here degrades to "go ask Postgres". A Valkey
 *      outage must make the dashboard slower, never wrong and never down.
 *   2. There is exactly ONE write function in this file: `enqueueAnalysis`, which XADDs a job. It
 *      does not touch the cluster, does not write to Postgres, and cannot resize anything. Anyone
 *      auditing this API's write surface has one function to read.
 */
import { createClient, type RedisClientType } from 'redis';

import { env } from '../env';
import * as keys from './keys';

declare global {
  // eslint-disable-next-line no-var
  var __ktRedis: RedisClientType | undefined;
}

let connecting: Promise<RedisClientType> | null = null;

async function getClient(): Promise<RedisClientType | null> {
  if (!env.CACHE_ENABLED) return null;
  if (globalThis.__ktRedis?.isReady) return globalThis.__ktRedis;

  // Single-flight. Without it, a burst of concurrent cache misses on a cold start each open their
  // own connection.
  connecting ??= (async () => {
    const client: RedisClientType = createClient({
      url: env.REDIS_URL,
      socket: {
        // Short: a cache is an optimisation, and waiting 5s for one is worse than the miss.
        connectTimeout: 2_000,
        // Bounded reconnect backoff. `false` here would give up permanently after one blip and
        // leave the dashboard uncached until a redeploy.
        reconnectStrategy: (retries) => Math.min(retries * 200, 5_000),
      },
    });
    client.on('error', (err) => {
      // Logged, never thrown. An unhandled 'error' event would take the process down over a cache.
      console.warn('[cache] redis error', err instanceof Error ? err.message : err);
    });
    await client.connect();
    globalThis.__ktRedis = client;
    return client;
  })();

  try {
    return await connecting;
  } catch (err) {
    console.warn('[cache] unavailable, running uncached', err);
    connecting = null;
    return null;
  }
}

/**
 * Cache-aside.
 *
 * Ordering: try cache, compute on a miss, then write back. A cache-write failure does not fail the
 * request -- the caller already has its answer.
 */
export async function cached<T>(key: string, ttlSeconds: number, compute: () => Promise<T>): Promise<T> {
  const client = await getClient();

  if (client) {
    try {
      const hit = await client.get(key);
      if (hit !== null) {
        try {
          return JSON.parse(hit) as T;
        } catch {
          // Corrupt entry: a miss, and also garbage. Drop it so the next reader does not pay the
          // same failed parse forever.
          await client.del(key).catch(() => undefined);
        }
      }
    } catch (err) {
      console.warn(`[cache] read failed for ${key}, treating as miss`, err);
    }
  }

  const value = await compute();

  if (client && value !== null && value !== undefined) {
    try {
      // setEx, never a bare set: every key here describes something that goes stale, and a key
      // without an expiry is a slow leak whose symptom appears weeks later as an eviction storm.
      await client.setEx(key, ttlSeconds, JSON.stringify(value));
    } catch (err) {
      console.warn(`[cache] write failed for ${key}, continuing uncached`, err);
    }
  }

  return value;
}

/**
 * XADD a job onto `analysis-jobs`. THE ONLY WRITE IN THE ENTIRE DASHBOARD API.
 *
 * The runId is minted HERE, by the producer, so the route can return `202 { runId }` immediately.
 * Letting the consumer mint it would mean having nothing to return until a worker woke up -- and
 * the analyser runs at `minReplicaCount: 0`, so that could be a cold start away.
 *
 * Not retried. A retry would enqueue the analysis twice; the caller is told it failed and can
 * decide for itself.
 */
export async function enqueueAnalysis(input: {
  cluster: string;
  window: string;
  namespaces?: string[];
  trigger?: string;
}): Promise<string> {
  const client = await getClient();
  if (!client) {
    throw new Error('cannot reach the job stream; analysis was not enqueued');
  }

  const runId = `run-${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`;

  await client.xAdd(
    keys.STREAM_ANALYSIS_JOBS,
    '*',
    {
      run_id: runId,
      cluster: input.cluster,
      window: input.window,
      namespaces: JSON.stringify(input.namespaces ?? []),
      trigger: input.trigger ?? 'api',
      // The API never authorises a PR. Even if the caller asked, this value stays false: opening a
      // PR is the analyser's decision under its own configuration, not a dashboard user's.
      auto_pr: 'false',
      enqueued_at: String(Math.floor(Date.now() / 1000)),
    },
    // Jobs are transient. Unbounded retention would grow forever with no reader.
    { TRIM: { strategy: 'MAXLEN', strategyModifier: '~', threshold: 10_000 } },
  );

  return runId;
}

/** Queue depth, for the health endpoint. Returns null when unknown -- never 0. */
export async function queueDepth(): Promise<number | null> {
  const client = await getClient();
  if (!client) return null;
  try {
    const groups = (await client.xInfoGroups(keys.STREAM_ANALYSIS_JOBS)) as {
      name: string;
      lag: number | null;
    }[];
    const group = groups.find((g) => g.name === keys.CONSUMER_GROUP);
    return group?.lag ?? null;
  } catch {
    // The stream may not exist yet, which is not the same as "no backlog".
    return null;
  }
}

export async function cacheHealthy(): Promise<boolean> {
  const client = await getClient();
  if (!client) return false;
  try {
    await client.ping();
    return true;
  } catch {
    return false;
  }
}
