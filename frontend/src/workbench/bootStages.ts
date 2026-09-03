/* Boot sequence stage copy — shared by WorkbenchBoot (renders it) and
   Workbench.tsx (times bootIdx off its length). Split from WorkbenchBoot.tsx
   so that file can stay component-only (react-refresh/only-export-components). */
export const BOOT_STAGES = [
  'Authenticating — JWT issued (12h scope)',
  'Loading workspace acme-corp',
  'Verifying ledger chain (sha256)',
  'Subscribing to live streams (kafka erp.*)',
  'Restoring cockpit layout',
];
