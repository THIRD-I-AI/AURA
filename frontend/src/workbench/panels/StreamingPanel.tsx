/* Streaming — native terminal-authority panel (replaces embedded classic
   StreamingPanel). Real streaming pipelines from GET /streaming/pipelines via
   streamingService, styled to match the Cockpit. */
import { useCallback, useEffect, useState } from 'react';
import { streamingService } from '../../services/api';

type Pipeline = {
  id: string; name?: string; description?: string; status?: string;
  event_time_field?: string; watermark_delay_seconds?: number;
  sinks?: unknown[]; transforms?: unknown[];
};

function statusColor(s?: string): string {
  const v = (s || '').toLowerCase();
  if (v === 'running' || v === 'active') return 'var(--accent)';
  if (v === 'error' || v === 'failed') return 'var(--danger)';
  if (v === 'paused' || v === 'stopped') return 'var(--warn)';
  return 'var(--text3)';
}

export default function StreamingPanel() {
  const [items, setItems] = useState<Pipeline[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await streamingService.list();
      setItems((resp.pipelines ?? []) as Pipeline[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list streaming pipelines.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const count = items?.length ?? 0;

  return (
    <div data-testid="wb-streaming-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {items === null ? 'loading…' : `${count} streaming pipeline${count === 1 ? '' : 's'} · watermark-driven, self-healing`}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={load} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '7px 14px' }}>↻ REFRESH</button>
      </div>

      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        {items === null && <div style={{ padding: '14px 16px', fontSize: 11.5, color: 'var(--text3)' }}>Loading pipelines…</div>}
        {items !== null && count === 0 && !error && (
          <div style={{ padding: '22px 16px', fontSize: 12, color: 'var(--text3)', textAlign: 'center', lineHeight: 1.7 }}>
            No streaming pipelines yet.<br />Define one over a Kafka/Redpanda topic — MAPE-K drift repair keeps it self-healing.
          </div>
        )}
        {(items ?? []).map((p) => (
          <div key={p.id} style={{ display: 'flex', flexDirection: 'column', gap: 5, padding: '11px 16px', borderTop: '1px solid var(--hair)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 6, height: 6, flex: 'none', background: statusColor(p.status), borderRadius: 0 }} />
              <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name || p.id}</span>
              <div style={{ flex: 1 }} />
              <span className="aw-mono" style={{ fontSize: 9, fontWeight: 700, letterSpacing: '.06em', color: statusColor(p.status) }}>{(p.status || 'idle').toUpperCase()}</span>
            </div>
            <div className="aw-mono" style={{ fontSize: 9.5, color: 'var(--text3)' }}>
              {(p.sinks?.length ?? 0)} sink{(p.sinks?.length ?? 0) === 1 ? '' : 's'} · {(p.transforms?.length ?? 0)} transform{(p.transforms?.length ?? 0) === 1 ? '' : 's'}
              {typeof p.watermark_delay_seconds === 'number' && ` · watermark ${p.watermark_delay_seconds}s`}
              {p.event_time_field && ` · event-time ${p.event_time_field}`}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
