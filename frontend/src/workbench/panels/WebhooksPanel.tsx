/* Webhooks — native terminal-authority panel (replaces embedded classic
   WebhooksPanel). Real outbound webhooks from GET /webhooks via webhookService,
   styled to match the Cockpit. */
import { useCallback, useEffect, useState } from 'react';
import { webhookService } from '../../services/api';

type Webhook = { id: string; url: string; events: string[]; active: boolean; retries: number; description?: string };

export default function WebhooksPanel() {
  const [hooks, setHooks] = useState<Webhook[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await webhookService.list();
      setHooks((resp.webhooks ?? []) as Webhook[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list webhooks.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const count = hooks?.length ?? 0;

  return (
    <div data-testid="wb-webhooks-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {hooks === null ? 'loading…' : `${count} outbound webhook${count === 1 ? '' : 's'} · HMAC-signed`}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={load} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '7px 14px' }}>↻ REFRESH</button>
      </div>

      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        {hooks === null && <div style={{ padding: '14px 16px', fontSize: 11.5, color: 'var(--text3)' }}>Loading webhooks…</div>}
        {hooks !== null && count === 0 && !error && (
          <div style={{ padding: '22px 16px', fontSize: 12, color: 'var(--text3)', textAlign: 'center', lineHeight: 1.7 }}>
            No webhooks configured.<br />Register an endpoint to receive HMAC-signed events (audit sealed, drift healed, pipeline completed).
          </div>
        )}
        {(hooks ?? []).map((h) => (
          <div key={h.id} style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '11px 16px', borderTop: '1px solid var(--hair)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 6, height: 6, flex: 'none', background: h.active ? 'var(--accent)' : 'var(--text3)', borderRadius: 0 }} />
              <span className="aw-mono" style={{ fontSize: 12, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.url}</span>
              <div style={{ flex: 1 }} />
              <span className="aw-mono" style={{ fontSize: 9, fontWeight: 700, letterSpacing: '.06em', color: h.active ? 'var(--accent)' : 'var(--text3)' }}>{h.active ? 'ACTIVE' : 'PAUSED'}</span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {(h.events ?? []).map((ev) => (
                <span key={ev} className="aw-mono" style={{ fontSize: 9.5, color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--hair)', padding: '2px 7px' }}>{ev}</span>
              ))}
              <span className="aw-mono" style={{ fontSize: 9.5, color: 'var(--text3)' }}>retries {h.retries}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
