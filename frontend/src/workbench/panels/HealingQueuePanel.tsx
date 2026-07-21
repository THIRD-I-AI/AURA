/* Healing Queue — native terminal-authority panel (replaces embedded classic
   HealingQueue page). Real risk-tiered self-healing recoveries awaiting human
   approval from GET /uasr/recovery/pending via healingService, with approve /
   reject. Styled to match the Cockpit. */
import { useCallback, useEffect, useState } from 'react';
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
    <div data-testid="wb-healing-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {pending === null ? 'loading…' : `${count} recover${count === 1 ? 'y' : 'ies'} awaiting approval · MAPE-K drift repair`}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={load} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '7px 14px' }}>↻ REFRESH</button>
      </div>

      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        {pending === null && <div style={{ padding: '14px 16px', fontSize: 11.5, color: 'var(--text3)' }}>Loading healing queue…</div>}
        {pending !== null && count === 0 && !error && (
          <div style={{ padding: '22px 16px', fontSize: 12, color: 'var(--text3)', textAlign: 'center', lineHeight: 1.7 }}>
            Queue clear — no recoveries awaiting approval.<br />When drift is detected, high-risk shims land here for a human decision (WORM-logged).
          </div>
        )}
        {(pending ?? []).map((r) => (
          <div key={r.id} style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '12px 16px', borderTop: '1px solid var(--hair)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 6, height: 6, flex: 'none', background: 'var(--warn)', borderRadius: 0 }} />
              <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>{r.source_id || r.drift_event_id}</span>
              <div style={{ flex: 1 }} />
              <span className="aw-mono" style={{ fontSize: 9, fontWeight: 700, letterSpacing: '.06em', color: 'var(--warn)' }}>{(r.status || 'pending').toUpperCase()}</span>
            </div>
            {r.diagnosis && <div style={{ fontSize: 11.5, color: 'var(--text2)', lineHeight: 1.5 }}>{r.diagnosis}</div>}
            <div className="aw-mono" style={{ fontSize: 9.5, color: 'var(--text3)' }}>
              {r.generation_method}
              {r.validation_passed != null && ` · validation ${r.validation_passed ? 'passed' : 'FAILED'}`}
              {typeof r.post_kl_divergence === 'number' && ` · post-KL ${r.post_kl_divergence.toFixed(4)}`}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={() => act(r.id, 'approve')} disabled={busy === r.id} className="aw-mono aw-hover-accent-bd" style={{ cursor: busy === r.id ? 'default' : 'pointer', fontSize: 10.5, fontWeight: 600, letterSpacing: '.04em', color: 'var(--accent)', background: 'var(--sunken)', border: '1px solid var(--accent-bd)', borderRadius: 0, padding: '5px 12px' }}>APPROVE</button>
              <button onClick={() => act(r.id, 'reject')} disabled={busy === r.id} className="aw-mono" style={{ cursor: busy === r.id ? 'default' : 'pointer', fontSize: 10.5, fontWeight: 600, letterSpacing: '.04em', color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '5px 12px' }}>REJECT</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
