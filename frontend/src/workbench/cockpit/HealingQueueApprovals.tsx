/* Healing queue — real S41 HITL approve/reject on pending drift recoveries.
   `healing` + `decideHeal` stay owned by Workbench.tsx: pendingCount (derived
   from `healing`) also feeds the nav badge, stat tiles, and the radar model. */
import { cn } from '../../lib/cn';
import type { Heal } from './types';

type Props = {
  healing: Heal[];
  pendingCount: number;
  decideHeal: (id: string, ok: boolean) => void;
};

export function HealingQueueApprovals({ healing, pendingCount, decideHeal }: Props) {
  return (
    <div className="aw-panel" data-testid="wb-healing">
      <div className="aw-panel-head">
        <div className="aw-panel-title">Healing queue</div>
        {pendingCount > 0
          ? <div className="aw-chip text-[var(--warn)] bg-[var(--warn-dim)]" style={{ fontWeight: 600 }}>{pendingCount} PENDING_APPROVAL</div>
          : <div className="aw-chip aw-pill-accent" style={{ fontWeight: 600 }}>QUEUE CLEAR</div>}
      </div>
      <div className="pt-1.5 px-4 pb-3.5">
        {healing.length === 0 && (
          <div className="py-3.5 text-xs text-[var(--text3)] leading-[1.6]">
            No pending recoveries — the MAPE-K loop is nominal. Drift proposals appear here for signed approval.
          </div>
        )}
        {healing.map((h) => (
          <div key={h.id} className="py-[11px] border-b border-[var(--hair)]">
            <div className="flex items-center gap-2">
              <div className="aw-mono text-[11.5px] font-medium">{h.title}</div>
              <div className={cn('aw-mono text-[9px] font-bold rounded-none py-px px-[7px] border', h.safe ? 'text-[var(--accent)] bg-[var(--accent-dim)] border-[var(--accent)]' : 'text-[var(--warn)] bg-[var(--warn-dim)] border-[var(--warn)]')}>{h.method}</div>
            </div>
            <div className="mt-[5px] text-[11px] text-[var(--text3)]">{h.sub}</div>
            {h.state === 'pending' && (
              <div className="mt-2 flex gap-[7px]">
                <div onClick={() => decideHeal(h.id, true)} className="cursor-pointer text-[11px] font-semibold text-[var(--accent)] bg-[var(--accent-dim)] border border-[var(--accent-bd)] rounded-none py-1 px-3">Approve & deploy</div>
                <div onClick={() => decideHeal(h.id, false)} className="cursor-pointer text-[11px] font-semibold text-[var(--danger)] bg-[var(--danger-dim)] border border-[var(--danger)] rounded-none py-1 px-3">Reject</div>
              </div>
            )}
            {h.resolution && <div className={cn('aw-mono mt-2 text-[10.5px] font-medium', h.state === 'deployed' ? 'text-[var(--accent)]' : 'text-[var(--danger)]')}>{h.resolution}</div>}
          </div>
        ))}
        <div className="pt-2.5 text-[10.5px] text-[var(--text3)]">every approve/reject is a signed override in the WORM audit log</div>
      </div>
    </div>
  );
}
