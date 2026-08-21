/**
 * problem.ts -- the single error path for the whole API, per RFC 9457 (Problem Details for HTTP
 * APIs, which obsoleted RFC 7807).
 *
 * One helper rather than ad-hoc `NextResponse.json({ error })` at each throw site, for two reasons
 * that are really the same reason:
 *
 *   1. A client can parse one shape. Three hand-rolled error shapes across twelve routes means the
 *      UI needs three code paths and gets one of them wrong.
 *   2. Leaks are prevented in ONE place. `internal()` deliberately discards the caught error's
 *      message from the response body and logs it server-side instead -- a driver error string is
 *      exactly the sort of thing that contains a connection URI, and therefore a password.
 */
import { NextResponse } from 'next/server';

/** RFC 9457 members, plus the extensions this API adds. */
export interface ProblemDetail {
  /** A URI reference identifying the problem TYPE, stable across occurrences. */
  type: string;
  /** Short, human-readable, and the same for every occurrence of this type. */
  title: string;
  status: number;
  /** Specific to THIS occurrence. */
  detail?: string;
  /** URI identifying this specific occurrence -- here, the request path. */
  instance?: string;
  /** Field-level validation failures, when the problem is a bad request. */
  errors?: { path: string; message: string }[];
}

const BASE = 'https://kubethrifty.io/problems';

/** RFC 9457 mandates this content type; a plain application/json defeats content negotiation. */
const CONTENT_TYPE = 'application/problem+json';

export function problem(
  status: number,
  type: string,
  title: string,
  detail?: string,
  extra?: Partial<ProblemDetail>,
): NextResponse<ProblemDetail> {
  const body: ProblemDetail = {
    type: `${BASE}/${type}`,
    title,
    status,
    ...(detail !== undefined ? { detail } : {}),
    ...extra,
  };
  return NextResponse.json(body, {
    status,
    headers: { 'content-type': CONTENT_TYPE },
  });
}

export const badRequest = (detail: string, errors?: ProblemDetail['errors']) =>
  problem(400, 'validation-failed', 'Request validation failed', detail, errors ? { errors } : {});

export const notFound = (what: string) =>
  problem(404, 'not-found', 'Resource not found', `${what} does not exist.`);

/**
 * 502 rather than 500: the failure is a dependency's, and the distinction matters operationally.
 * A 500 sends somebody to read the dashboard's logs; a 502 names Prometheus or the database.
 */
export const upstreamUnavailable = (dependency: string, detail?: string) =>
  problem(
    502,
    'upstream-unavailable',
    `${dependency} is unavailable`,
    detail ?? `The dashboard could not reach ${dependency}. This is a read-only failure: no ` +
      `cluster state was changed.`,
  );

/**
 * 503 with Retry-After. What the circuit breaker returns while open -- an honest "not now" rather
 * than a slow queue of requests all waiting to time out against the same dead dependency.
 */
export function circuitOpen(dependency: string, retryAfterSeconds = 30): NextResponse<ProblemDetail> {
  const res = problem(
    503,
    'circuit-open',
    `${dependency} calls are suspended`,
    `Repeated failures against ${dependency} tripped the circuit breaker. Retry shortly.`,
  );
  res.headers.set('retry-after', String(retryAfterSeconds));
  return res;
}

export const conflict = (detail: string) =>
  problem(409, 'conflict', 'Conflicting request', detail);

/**
 * The catch-all. The caught error is LOGGED, never returned: driver and client errors routinely
 * embed connection strings, and a connection string embeds a password.
 */
export function internal(context: string, cause: unknown): NextResponse<ProblemDetail> {
  console.error(`[${context}]`, cause);
  return problem(
    500,
    'internal-error',
    'Internal server error',
    `The request failed while handling ${context}. The failure has been logged.`,
  );
}

/**
 * Turn any thrown value into a ProblemDetail response.
 *
 * Every route handler funnels its catch block through here, so no route can accidentally return a
 * bare stack trace.
 */
export function toProblem(context: string, err: unknown): NextResponse<ProblemDetail> {
  if (err instanceof CircuitOpenError) return circuitOpen(err.dependency);
  if (err instanceof UpstreamError) return upstreamUnavailable(err.dependency, err.message);
  if (err instanceof NotFoundError) return notFound(err.what);
  return internal(context, err);
}

export class UpstreamError extends Error {
  constructor(readonly dependency: string, message?: string) {
    super(message ?? `${dependency} did not respond successfully`);
    this.name = 'UpstreamError';
  }
}

export class CircuitOpenError extends Error {
  constructor(readonly dependency: string) {
    super(`circuit open for ${dependency}`);
    this.name = 'CircuitOpenError';
  }
}

export class NotFoundError extends Error {
  constructor(readonly what: string) {
    super(`${what} not found`);
    this.name = 'NotFoundError';
  }
}
