/**
 * resilience.ts -- timeout, retry with jittered backoff, and a circuit breaker.
 *
 * Hand-rolled rather than pulling in `opossum`, because the whole implementation is ~80 lines and
 * the semantics matter more than the dependency: what counts as a failure, what is safe to retry,
 * and how long a tripped breaker stays open are decisions this file needs to state out loud.
 *
 * The rules, in order of importance:
 *
 *   * NEVER retry a non-idempotent call. `withRetry` is used on reads only. Retrying the XADD that
 *     triggers an analysis would enqueue the run twice.
 *   * Jitter is not optional. Constant backoff synchronises every client into a thundering herd
 *     that arrives together, fails together, and retries together.
 *   * A timeout without an AbortSignal is a lie: the promise settles but the socket stays open,
 *     so a slow dependency still consumes connections until it decides to answer.
 */
import { CircuitOpenError, UpstreamError } from './problem';

export async function withTimeout<T>(
  ms: number,
  dependency: string,
  fn: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fn(controller.signal);
  } catch (err) {
    if (controller.signal.aborted) {
      throw new UpstreamError(dependency, `timed out after ${ms}ms`);
    }
    throw err;
  } finally {
    // Always cleared. A dangling timer keeps the event loop alive and, in a serverless runtime,
    // keeps the invocation billable.
    clearTimeout(timer);
  }
}

export interface RetryOptions {
  attempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
}

/**
 * Retry with full-jitter exponential backoff.
 *
 * READS ONLY. There is no `withRetry` around any write in this codebase, and there is exactly one
 * write in this codebase.
 */
export async function withRetry<T>(fn: () => Promise<T>, opts: RetryOptions = {}): Promise<T> {
  const attempts = opts.attempts ?? 3;
  const base = opts.baseDelayMs ?? 100;
  const max = opts.maxDelayMs ?? 2_000;

  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (attempt === attempts - 1) break;
      // Full jitter: sleep a random amount in [0, backoff]. Spreads a synchronised herd instead of
      // rescheduling all of it to the same millisecond.
      const backoff = Math.min(max, base * 2 ** attempt);
      await sleep(Math.random() * backoff);
    }
  }
  throw lastError;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

type BreakerState = 'closed' | 'open' | 'half-open';

/**
 * A circuit breaker, per dependency.
 *
 * The point is not to protect this process; it is to stop hammering something that is already
 * failing, and to fail FAST while it recovers. Without one, a dead Prometheus turns every
 * dashboard request into an 8-second wait, and the queue of waiting requests exhausts the server
 * long before Prometheus comes back.
 *
 * `half-open` exists so recovery does not require a deploy: after the cooldown, exactly ONE probe
 * is allowed through. If it succeeds the breaker closes; if it fails the cooldown restarts. Letting
 * all traffic back in at once would re-trip it instantly and hurt a recovering dependency.
 */
export class CircuitBreaker {
  private failures = 0;
  private state: BreakerState = 'closed';
  private openedAt = 0;
  private probing = false;

  constructor(
    private readonly dependency: string,
    private readonly threshold = 5,
    private readonly cooldownMs = 30_000,
  ) {}

  async run<T>(fn: () => Promise<T>): Promise<T> {
    if (this.state === 'open') {
      if (Date.now() - this.openedAt < this.cooldownMs) {
        throw new CircuitOpenError(this.dependency);
      }
      this.state = 'half-open';
      this.probing = false;
    }

    if (this.state === 'half-open') {
      // Exactly one probe. Concurrent callers are refused rather than queued, so a recovering
      // dependency receives one request instead of everything that piled up during the outage.
      if (this.probing) throw new CircuitOpenError(this.dependency);
      this.probing = true;
    }

    try {
      const result = await fn();
      this.onSuccess();
      return result;
    } catch (err) {
      this.onFailure();
      throw err;
    }
  }

  private onSuccess(): void {
    this.failures = 0;
    this.state = 'closed';
    this.probing = false;
  }

  private onFailure(): void {
    this.probing = false;
    // A failed probe re-opens immediately, without waiting to re-reach the threshold: we already
    // have evidence this dependency is unhealthy.
    if (this.state === 'half-open') {
      this.trip();
      return;
    }
    this.failures++;
    if (this.failures >= this.threshold) this.trip();
  }

  private trip(): void {
    this.state = 'open';
    this.openedAt = Date.now();
    console.warn(`[breaker] ${this.dependency} open for ${this.cooldownMs}ms`);
  }

  get status(): { state: BreakerState; failures: number } {
    return { state: this.state, failures: this.failures };
  }
}
