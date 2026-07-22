/* Healing Queue — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md):
   ui-kit primitives + token utilities, no inline styles. Real risk-tiered
   self-healing recoveries awaiting human approval from GET /uasr/recovery/pending
   via healingService, with approve / reject. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { healingService } from '../../services/api';

type Recovery = {
  id: string; drift_event_id: string; source_id: string | null; status: string;
  diagnosis: string | null; generation_method: string;
  validation_passed: boolean | null; post_kl_divergence: number | null;
};

export default function HealingQueuePanel() {
  const [pending, setPending] = useState<Recovery[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await healingService.pending();
      setPending(res as Recovery[]);
      setError(null);
    } catch {
      setError('Could not reach the UASR service to load the healing queue.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const act = useCallback(async (id: string, kind: 'approve' | 'reject') => {
    setBusy(id);
    try {
      const approver = 'workbench-operator';
      if (kind === 'approve') await healingService.approve(id, approver, 'approved via workbench');
      else await healingService.reject(id, approver, 'rejected via workbench');
      await load();
    } catch {
      setError(`Could not ${kind} recovery ${id}.`);
    } finally {
      setBusy(null);
    }
  }, [load]);

  const count = pending?.length ?? 0;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-healing-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {pending === null ? 'loading…' : `${count} recover${count === 1 ? 'y' : 'ies'} awaiting approval · MAPE-K drift repair`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <Panel>
        {pending === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading healing queue…</div>}
        {pending !== null && count === 0 && !error && (
          <EmptyState intent="awaiting" title="Queue clear" description="No recoveries awaiting approval. When drift is detected, high-risk shims land here for a human decision (WORM-logged)." />
        )}
        {(pending ?? []).map((r, i) => (
          <div key={r.id} className={cn('flex flex-col gap-2 px-4 py-3', i > 0 && 'border-t border-border')}>
            <div className="flex items-center gap-2.5">
              <span className="size-1.5 shrink-0 bg-warn" />
              <span className="text-sm font-semibold text-card-foreground">{r.source_id || r.drift_event_id}</span>
              <div className="flex-1" />
              <span className="font-mono text-2xs font-bold tracking-wider text-warn">{(r.status || 'pending').toUpperCase()}</span>
            </div>
            {r.diagnosis && <div className="text-xs leading-snug text-text-secondary">{r.diagnosis}</div>}
            <div className="font-mono text-2xs text-text-tertiary">
              {r.generation_method}
              {r.validation_passed != null && ` · validation ${r.validation_passed ? 'passed' : 'FAILED'}`}
              {typeof r.post_kl_divergence === 'number' && ` · post-KL ${r.post_kl_divergence.toFixed(4)}`}
            </div>
            <div className="flex gap-2">
              <Button size="xs" onClick={() => act(r.id, 'approve')} disabled={busy === r.id}>Approve</Button>
              <Button variant="outline" size="xs" className="text-danger" onClick={() => act(r.id, 'reject')} disabled={busy === r.id}>Reject</Button>
            </div>
          </div>
        ))}
      </Panel>
    </div>
  );
}
