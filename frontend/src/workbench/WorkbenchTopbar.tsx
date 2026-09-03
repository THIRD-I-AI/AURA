/* Top chrome bar — burger, workspace chip, gateway-offline pill, ⌘K launcher,
   user menu. Purely presentational; all state (nav-open, palette) is owned by
   Workbench.tsx and passed down. */
import { UserMenu } from '../auth/UserMenu';
import { getCurrentWorkspaceId } from '../services/api';

type Props = {
  onToggleNav: () => void;
  gatewayUp: boolean | null;
  onOpenPalette: () => void;
};

export function WorkbenchTopbar({ onToggleNav, gatewayUp, onOpenPalette }: Props) {
  return (
    <div className="flex items-center gap-4 h-[54px] px-6 bg-[var(--surface)] border-b border-[var(--border)] flex-none">
      <div className="aw-burger" onClick={onToggleNav} role="button" aria-label="Toggle navigation">☰</div>
      <div className="flex items-center gap-[9px]"><span className="w-2 h-2 bg-[var(--accent)] rounded-none" /><span className="aw-display font-bold text-[15px] tracking-widest">AURA</span></div>
      <div className="flex items-center gap-2 py-1 pr-2.5 pl-1 text-[12.5px] text-[var(--text2)] border border-[var(--border)] rounded-none">
        <span className="aw-mono w-[18px] h-[18px] grid place-items-center text-[9.5px] font-bold text-[var(--accent)] bg-[var(--accent-dim)] border border-[var(--accent-bd)]">{getCurrentWorkspaceId().slice(0, 2).toUpperCase()}</span>
        {getCurrentWorkspaceId()}
      </div>
      {gatewayUp === false && <div className="aw-mono text-[9.5px] font-semibold tracking-[0.08em] text-[var(--danger)] bg-[var(--sunken)] border border-[var(--border)] rounded-none py-[3px] px-[7px]">GATEWAY OFFLINE</div>}
      <div className="flex-1" />
      <div onClick={onOpenPalette} className="aw-mono aw-hover-accent-bd aw-topbar-search cursor-pointer flex items-center gap-2 text-[11px] font-medium text-[var(--text2)] border border-[var(--border)] rounded-none py-[5px] px-2.5">
        Search, ask, or run a command <span className="bg-[var(--sunken)] rounded-none py-px px-[5px]">⌘K</span>
      </div>
      <UserMenu />
    </div>
  );
}
