/* Bottom-center toast — Workbench.tsx owns the message + auto-dismiss timer. */

export function Toast({ toast }: { toast: string | null }) {
  if (!toast) return null;
  return (
    <div className="fixed bottom-[26px] left-1/2 -translate-x-1/2 z-[200] bg-[var(--raised)] border border-[var(--accent-bd)] text-[var(--text)] font-ui font-medium text-[12px] rounded-none py-2.5 px-[18px] shadow-[0_8px_30px_rgba(0,0,0,.3)] animate-[awup_.2s_ease] flex items-center gap-2" data-testid="wb-toast">
      <span className="text-[var(--accent)]">✓</span>{toast}
    </div>
  );
}
