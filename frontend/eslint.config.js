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
      // `.configs.flat[...]`, not `.configs[...]`. From v7 the plugin ships BOTH
      // shapes: the top-level one keeps the legacy eslintrc form
      // (`plugins: ['react-hooks']`, an array), which flat config rejects with
      // "Key plugins: Expected an object". The flat namespace is the one whose
      // `plugins` is keyed by name with the plugin object as the value.
      reactHooks.configs.flat['recommended-latest'],
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
      // eslint-plugin-react-hooks v7 bundles the React Compiler lint suite.
      // These five rules did NOT exist in v5 and flag 39 pre-existing sites:
      // set-state-in-effect (23), refs (12), immutability (2), purity (1),
      // use-memo (1). None of them is rules-of-hooks or exhaustive-deps, so
      // turning them off keeps enforcement exactly where v5 had it — the bump
      // to v7 was needed for eslint 10 peer support, and quietly folding a
      // 39-site refactor into a dependency upgrade is how upgrades stall.
      // Adopting them is worthwhile, but as its own change with its own review.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/immutability': 'off',
      'react-hooks/purity': 'off',
      'react-hooks/use-memo': 'off',
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
