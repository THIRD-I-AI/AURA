/* Left nav rail — grouped destinations, collapse toggle, mobile drawer, and
   the ledger-status footer. Nav destinations and their icons live in
   navConfig.ts, shared with CommandPalette's "Go to X" commands. */
import { cn } from '../lib/cn';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { NAV_GROUPS, NAV_ICONS } from './navConfig';

type Props = {
  navOpen: boolean;
  onCloseNav: () => void;
  navCollapsed: boolean;
  onToggleCollapsed: () => void;
  nav: string;
  selectNav: (name: string) => void;
  pendingCount: number;
  ledger: { no: string; hash: string; intact: boolean } | null;
  ledgerDown: boolean;
};

export function WorkbenchNav({ navOpen, onCloseNav, navCollapsed, onToggleCollapsed, nav, selectNav, pendingCount, ledger, ledgerDown }: Props) {
  return (
    <div className={cn('aw-nav', navOpen && 'aw-open', 'flex flex-none flex-col gap-[18px] overflow-y-auto overflow-x-hidden border-r border-[var(--border)] bg-[var(--surface)] pt-2.5 transition-[width] duration-[160ms] ease-[var(--ease-out)]', navCollapsed ? 'w-14 px-1.5 pb-4' : 'w-[204px] px-2.5 pb-5')}>
      <button
        type="button"
        onClick={onToggleCollapsed}
        className={cn('aw-hover-raise aw-topbar-search grid place-items-center w-[22px] h-[22px] border border-[var(--border)] bg-transparent text-[var(--text3)] cursor-pointer flex-none', navCollapsed ? 'self-center' : 'self-end')}
        aria-label={navCollapsed ? 'Expand navigation' : 'Collapse navigation'}
        title={navCollapsed ? 'Expand navigation' : 'Collapse navigation'}
      >
        {navCollapsed ? <PanelLeftOpen size={13} /> : <PanelLeftClose size={13} />}
      </button>
      {NAV_GROUPS.map(([label, items]) => (
        <div key={label}>
          {!navCollapsed && <div className="aw-mono text-[9.5px] font-semibold tracking-[0.14em] text-[var(--text3)] px-3 pb-1.5">{label}</div>}
          <div className="flex flex-col gap-px">
            {items.map((name) => {
              const active = name === nav;
              const badge = (name === 'Exception Queue' || name === 'Healing Queue') && pendingCount > 0 ? String(pendingCount) : null;
              const goNav = () => { selectNav(name); onCloseNav(); };
              const Icon = NAV_ICONS[name];
              return (
                <div key={name} role="button" tabIndex={0} aria-current={active ? 'page' : undefined} onClick={goNav} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goNav(); } }} title={navCollapsed ? name : undefined} className={cn('aw-nav-item', active ? 'text-[var(--text)] bg-[var(--accent-dim)] font-semibold' : 'text-[var(--text2)] bg-transparent font-normal', navCollapsed ? 'justify-center' : 'justify-between')}>
                  <span className="flex items-center gap-2 min-w-0">
                    {Icon && <Icon size={15} className={cn('flex-none', active ? 'text-[var(--accent)]' : 'text-[var(--text3)]')} />}
                    {!navCollapsed && <span className="truncate">{name}</span>}
                  </span>
                  {!navCollapsed && badge && <span className="aw-mono text-[9.5px] font-semibold text-[var(--warn)] bg-[var(--warn-dim)] rounded-none py-px px-1.5">{badge}</span>}
                </div>
              );
            })}
          </div>
        </div>
      ))}
      {!navCollapsed && (
        <div className="aw-mono mt-auto pt-3.5 px-3 pb-0 border-t border-[var(--border)] text-[9.5px] font-medium text-[var(--text3)] leading-[1.9]">
          {ledger ? (<>LEDGER {ledger.no}<br /><span className={ledger.intact ? 'text-[var(--accent)]' : 'text-[var(--danger)]'}>● {ledger.intact ? 'CHAIN INTACT' : 'CHAIN BROKEN'}</span><br />sha256 {ledger.hash}</>) : (<>LEDGER —<br /><span className={ledgerDown ? 'text-[var(--warn)]' : undefined}>● {ledgerDown ? 'SERVICE OFFLINE' : 'VERIFYING…'}</span></>)}
        </div>
      )}
    </div>
  );
}
