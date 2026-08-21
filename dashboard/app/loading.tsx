/**
 * Route-level loading skeleton.
 *
 * Shapes mirror the real layout so the page does not jump when data arrives. Skeletons are neutral
 * grey, never the waste or pressure palettes -- a shimmering block in "safe to shrink" orange would
 * read as a finding for the fraction of a second before real data replaced it.
 */
export default function Loading() {
  return (
    <main className="mx-auto max-w-[1400px] px-6 py-10">
      <div className="mb-8 space-y-2">
        <div className="h-7 w-48 animate-pulse rounded bg-surface-2" />
        <div className="h-4 w-full max-w-3xl animate-pulse rounded bg-surface-2" />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-lg bg-surface-1" />
        ))}
      </div>

      <div className="mt-8 h-96 animate-pulse rounded-lg bg-surface-1" />
      <div className="mt-8 h-64 animate-pulse rounded-lg bg-surface-1" />
    </main>
  );
}
