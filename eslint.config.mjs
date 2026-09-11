import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import nextPlugin from '@next/eslint-plugin-next';
import reactHooks from 'eslint-plugin-react-hooks';

/**
 * Flat config. `next lint` is deprecated in Next 15, and `eslint-config-next`
 * still relies on a legacy patch that ESLint 9 rejects, so the Next and
 * react-hooks plugins are wired up directly instead.
 */
export default tseslint.config(
  {
    ignores: [
      '.next/**',
      'node_modules/**',
      'data/**',
      'seeds/**',
      'tests/fixtures/**',
      'next-env.d.ts',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    plugins: { '@next/next': nextPlugin, 'react-hooks': reactHooks },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs['core-web-vitals'].rules,
      ...reactHooks.configs.recommended.rules,
      // Unused args are fine when prefixed with _, which reads better than
      // deleting a parameter that documents a callback's shape.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    // The sync CLI is a console tool; logging is the point.
    files: ['scripts/**/*.ts'],
    rules: { 'no-console': 'off' },
  },
);
