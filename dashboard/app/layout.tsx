import type { Metadata, Viewport } from 'next';

import './globals.css';

/**
 * Root layout. A Server Component, like every layout and page in this app unless it demonstrably
 * needs browser state -- the default is server, and `'use client'` has to earn its place.
 */
export const metadata: Metadata = {
  title: {
    default: 'KubeThrifty',
    template: '%s | KubeThrifty',
  },
  description:
    'Kubernetes pod right-sizing advisor. Sizes CPU from percentiles and memory from the observed ' +
    'peak, rehearses candidate sizes on a live pod, and opens pull requests.',
  // This dashboard shows internal cluster data. Keeping it out of indexes is the right default even
  // though it should never be publicly reachable in the first place.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  colorScheme: 'dark',
  themeColor: '#14161f',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-surface-0 text-text-primary antialiased">{children}</body>
    </html>
  );
}
