import type { NextConfig } from 'next';

/**
 * Next.js configuration.
 *
 * `output: 'standalone'` is what makes the Docker image small: Next traces the actual module graph
 * and emits only the files the server needs, instead of shipping the whole node_modules tree.
 *
 * The security headers are set here rather than in an ingress annotation so they hold in local
 * development too -- a CSP that only exists in production is a CSP nobody tests.
 */
const nextConfig: NextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,

  // A type error must fail the build. Silencing it would defeat the point of the strict tsconfig,
  // where `noUncheckedIndexedAccess` is what turns "a missing metric became 0" into a compile error.
  // (Next 16 dropped the top-level `eslint` key; linting is a separate `npm run lint` step in CI.)
  typescript: { ignoreBuildErrors: false },

  // No remote images. Every asset is local, so the loader has nothing to fetch and cannot be
  // pointed at an internal address.
  images: { remotePatterns: [] },

  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'x-content-type-options', value: 'nosniff' },
          { key: 'x-frame-options', value: 'DENY' },
          { key: 'referrer-policy', value: 'strict-origin-when-cross-origin' },
          // This dashboard needs no camera, microphone or geolocation. Denying them is free.
          {
            key: 'permissions-policy',
            value: 'camera=(), microphone=(), geolocation=(), payment=()',
          },
          {
            key: 'content-security-policy',
            value: [
              "default-src 'self'",
              // Recharts injects inline styles for its SVG, so style-src needs 'unsafe-inline'.
              // script-src deliberately does NOT get it.
              "style-src 'self' 'unsafe-inline'",
              "script-src 'self'",
              "img-src 'self' data:",
              "font-src 'self'",
              // Same-origin only. The browser talks to this app's own routes; the Prometheus proxy
              // runs server-side, so the client never needs to reach Prometheus directly.
              "connect-src 'self'",
              "frame-ancestors 'none'",
              "base-uri 'self'",
              "form-action 'self'",
            ].join('; '),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
