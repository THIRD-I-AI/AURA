import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Pragmatic: the codebase uses `any` extensively in API types,
      // callbacks, and event handlers.  A separate typing effort will
      // progressively eliminate these.
      '@typescript-eslint/no-explicit-any': 'off',
      // Allow unused vars prefixed with _ (common for destructuring)
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
      }],
      // S37 security guards.
      'no-restricted-syntax': ['error',
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: 'dangerouslySetInnerHTML is banned — render text, or extend a sanctioned primitive (HashChip pattern).',
        },
        {
          selector: "JSXOpeningElement[name.name='a']:has(JSXAttribute[name.name='target'][value.value='_blank']):not(:has(JSXAttribute[name.name='rel']))",
          message: 'target="_blank" requires rel="noopener noreferrer" (reverse tabnabbing).',
        },
      ],
    },
  },
  {
    // shadcn/ui primitives (New York) canonically co-locate a `*Variants` cva
    // export beside the component in one file (e.g. button.tsx exports Button +
    // buttonVariants). That is a deliberate shadcn convention, but it trips
    // react-refresh/only-export-components. Scope the rule off for the ui-kit
    // primitives dir only — app code keeps the guard. Fast-refresh DX loss here
    // is nil: these files are stable vendored primitives, not actively hot-edited.
    files: ['src/components/ui-kit/**/*.{ts,tsx}'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])
