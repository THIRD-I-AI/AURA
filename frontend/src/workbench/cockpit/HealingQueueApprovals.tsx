/* Healing queue — real S41 HITL approve/reject on pending drift recoveries.
   `healing` + `decideHeal` stay owned by Workbench.tsx: pendingCount (derived
   from `healing`) also feeds the nav badge, stat tiles, and the radar model. */
import { useState } from 'react';
import { cn } from '../../lib/cn';
import type { Heal } from './types';

type Props = {
  healing: Heal[];
  pendingCount: number;
  decideHeal: (id: string, ok: boolean) => void;
};

const ACTION_CLASS = 'cursor-pointer text-[11px] font-semibold rounded-none py-1 px-3 outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50';

export function HealingQueueApprovals({ healing, pendingCount, decideHeal }: Props) {
  // BUG-143: a decision is a signed, WORM-logged override (approve also deploys
  // a shim), so the first click only arms it; a second, explicit click commits.
  const [armed, setArmed] = useState<{ id: string; ok: boolean } | null>(null);
  const commit = (id: string, ok: boolean) => { setArmed(null); decideHeal(id, ok); };
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
            {h.state === 'pending' && armed?.id === h.id && (
              <div className="mt-2 flex flex-wrap items-center gap-[7px]" role="group" aria-label={`Confirm ${armed.ok ? 'approval' : 'rejection'} of ${h.title}`}>
                <span className="text-[11px] text-[var(--text2)]">
                  {armed.ok ? 'Deploy this shim and sign the override?' : 'Reject this recovery and sign the override?'}
                </span>
                <button
                  type="button"
                  autoFocus
                  onClick={() => commit(h.id, armed.ok)}
                  className={cn(ACTION_CLASS, armed.ok
                    ? 'text-[var(--accent)] bg-[var(--accent-dim)] border border-[var(--accent-bd)]'
                    : 'text-[var(--danger)] bg-[var(--danger-dim)] border border-[var(--danger)]')}
                >
                  {armed.ok ? 'Confirm approve' : 'Confirm reject'}
                </button>
                <button
                  type="button"
                  onClick={() => setArmed(null)}
                  className={cn(ACTION_CLASS, 'text-[var(--text2)] bg-transparent border border-[var(--hair)]')}
                >
                  Cancel
                </button>
              </div>
            )}
            {h.state === 'pending' && armed?.id !== h.id && (
              <div className="mt-2 flex gap-[7px]">
                <button
                  type="button"
                  onClick={() => setArmed({ id: h.id, ok: true })}
                  className="cursor-pointer text-[11px] font-semibold text-[var(--accent)] bg-[var(--accent-dim)] border border-[var(--accent-bd)] rounded-none py-1 px-3 outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  Approve &amp; deploy
                </button>
                <button
                  type="button"
                  onClick={() => setArmed({ id: h.id, ok: false })}
                  className="cursor-pointer text-[11px] font-semibold text-[var(--danger)] bg-[var(--danger-dim)] border border-[var(--danger)] rounded-none py-1 px-3 outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  Reject
                </button>
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
