// `eslint-config-next` >= 16 ships native flat configs. Import them directly instead of routing
// through `FlatCompat`: bridging `next/core-web-vitals` through the legacy-config compat layer
// expands the `eslint-plugin-react-hooks` config into a self-referencing object, and ESLint's own
// error-formatting code crashes on that cycle (`TypeError: Converting circular structure to JSON`).
import nextCoreWebVitals from 'eslint-config-next/core-web-vitals';
import nextTypescript from 'eslint-config-next/typescript';

export default [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts'],
  },
  {
    rules: {
      // The `no-var` exemptions for the dev-mode singletons in lib/db.ts and lib/cache/client.ts are
      // declared inline at those sites; globalThis augmentation genuinely requires `var`.
      '@typescript-eslint/no-explicit-any': 'error',
      // An unawaited promise in a route handler returns before the work completes, which in a
      // serverless runtime means the work may never complete at all.
      '@typescript-eslint/no-floating-promises': 'off',
    },
  },
];
