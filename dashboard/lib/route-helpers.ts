/**
 * route-helpers.ts -- the shared shape of every route handler.
 *
 * Each handler is: validate -> call one service -> respond. This file holds the validate and respond
 * halves so no route re-implements them, because the interesting failure is a route that FORGETS
 * one: forgets to validate and passes a raw query string into SQL, or forgets to catch and returns
 * a stack trace.
 */
import { NextResponse } from 'next/server';
import type { z } from 'zod';

import { badRequest, toProblem } from './problem';

/**
 * Parse a URL's search params against a Zod schema.
 *
 * Repeated keys collapse to the last value, which is intentional: `?limit=10&limit=99999` should not
 * become an array that a `z.coerce.number()` silently coerces past its own max.
 */
export function parseQuery<S extends z.ZodType>(
  url: string,
  schema: S,
): { ok: true; data: z.infer<S> } | { ok: false; response: NextResponse } {
  const params = Object.fromEntries(new URL(url).searchParams.entries());
  const result = schema.safeParse(params);
  if (!result.success) {
    return {
      ok: false,
      response: badRequest(
        'One or more query parameters were rejected.',
        result.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })),
      ),
    };
  }
  return { ok: true, data: result.data };
}

export async function parseJsonBody<S extends z.ZodType>(
  request: Request,
  schema: S,
): Promise<{ ok: true; data: z.infer<S> } | { ok: false; response: NextResponse }> {
  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    // Distinguished from a schema failure: "not JSON" and "JSON with the wrong shape" are different
    // mistakes and deserve different messages.
    return { ok: false, response: badRequest('Request body must be valid JSON.') };
  }
  const result = schema.safeParse(raw);
  if (!result.success) {
    return {
      ok: false,
      response: badRequest(
        'The request body was rejected.',
        result.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })),
      ),
    };
  }
  return { ok: true, data: result.data };
}

export function parseParams<S extends z.ZodType>(
  params: Record<string, string | string[] | undefined>,
  schema: S,
): { ok: true; data: z.infer<S> } | { ok: false; response: NextResponse } {
  const result = schema.safeParse(params);
  if (!result.success) {
    return {
      ok: false,
      response: badRequest(
        'A path parameter was rejected.',
        result.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })),
      ),
    };
  }
  return { ok: true, data: result.data };
}

/**
 * Run a handler body, funnelling every thrown value through the RFC 9457 mapper.
 *
 * Wrapping is what guarantees no route can leak a driver error to a client. `context` names the
 * operation in the server log so a 500 is traceable without putting details in the response.
 */
export async function handle(
  context: string,
  // Intentionally not generic over the body type. A handler legitimately returns either a success
  // payload or a ProblemDetail, and a generic parameter would try to unify those into one type and
  // reject the union.
  fn: () => Promise<NextResponse<unknown>>,
): Promise<NextResponse> {
  try {
    return await fn();
  } catch (err) {
    return toProblem(context, err);
  }
}

/**
 * A cacheable JSON response.
 *
 * `s-maxage` with `stale-while-revalidate` lets a shared cache serve slightly old data instantly
 * while refreshing behind it -- the right trade for a dashboard, where a 30-second-old waste figure
 * is fine and a spinner is not. `private` is used where the payload should not be shared at all.
 */
export function json<T>(data: T, sMaxAgeSeconds = 0): NextResponse<T> {
  const headers: Record<string, string> = {};
  headers['cache-control'] =
    sMaxAgeSeconds > 0
      ? `public, s-maxage=${sMaxAgeSeconds}, stale-while-revalidate=${sMaxAgeSeconds * 2}`
      : 'no-store';
  return NextResponse.json(data, { headers });
}
