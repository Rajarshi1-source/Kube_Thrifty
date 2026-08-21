import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="mx-auto flex max-w-2xl flex-col items-start gap-4 px-6 py-24">
      <h1 className="text-xl font-semibold">Not found</h1>
      <p className="text-sm text-text-secondary">
        No analysed workload matches that name. A workload appears here only after an analysis has
        recorded a recommendation for it &mdash; containers with no declared resource request are
        skipped, because without one there is no denominator and no waste to measure.
      </p>
      <Link href="/" className="text-sm underline hover:text-text-primary">
        Back to recommendations
      </Link>
    </main>
  );
}
