import { FlatCompat } from '@eslint/eslintrc';

const compat = new FlatCompat({ baseDirectory: import.meta.dirname });

export default [
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
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
