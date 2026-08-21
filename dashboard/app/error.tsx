'use client';

/**
 * Route-level error boundary. A Client Component -- error boundaries need state and an event
 * handler, so this is one of the few places `'use client'` genuinely earns its place.
 *
 * It shows the error's `digest`, not its message. Next.js redacts server error messages in
 * production and replaces them with a digest that correlates to a server log line, which is exactly
 * the right trade: the operator can find the detail, and the browser never receives a stack trace
 * that might contain a connection string.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="mx-auto flex max-w-2xl flex-col items-start gap-4 px-6 py-24">
      <h1 className="text-xl font-semibold">This view could not be loaded</h1>
      <p className="text-sm text-text-secondary">
        The failure was logged server-side. Nothing in the cluster was changed &mdash; this dashboard
        is read-only and holds no cluster credentials.
      </p>
      {error.digest && (
        <p className="font-mono text-xs text-text-muted">digest: {error.digest}</p>
      )}
      <button
        type="button"
        onClick={reset}
        className="rounded-md border border-border-strong bg-surface-2 px-4 py-2 text-sm hover:bg-surface-3"
      >
        Retry
      </button>
    </main>
  );
}
