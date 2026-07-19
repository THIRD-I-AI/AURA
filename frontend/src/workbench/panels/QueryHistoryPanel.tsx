/* Query History — native terminal-authority panel (replaces embedded classic
   QueryHistory page). Lists real executed queries from GET /query-history via
   analyticsService, styled to match the Cockpit. */
import { useCallback, useEffect, useState } from 'react';
import { analyticsService } from '../../services/api';

type QueryRow = {
  prompt?: string; sql?: string; status?: string;
  row_count?: number | null; execution_time_ms?: number | null; timestamp?: string;
};

function statusColor(s?: string): string {
  if (s === 'success') return 'var(--accent)';
  if (s === 'error' || s === 'failed') return 'var(--danger)';
  return 'var(--warn)';
}

export default function QueryHistoryPanel() {
  const [rows, setRows] = useState<QueryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await analyticsService.getQueryHistory(50);
      setRows((resp.queries ?? []) as QueryRow[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to load query history.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const count = rows?.length ?? 0;

  return (
    <div data-testid="wb-queries-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {rows === null ? 'loading…' : `${count} quer${count === 1 ? 'y' : 'ies'} · this workspace`}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={load} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '7px 14px' }}>↻ REFRESH</button>
      </div>

      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        {rows === null && <div style={{ padding: '14px 16px', fontSize: 11.5, color: 'var(--text3)' }}>Loading query history…</div>}
        {rows !== null && count === 0 && !error && (
          <div style={{ padding: '22px 16px', fontSize: 12, color: 'var(--text3)', textAlign: 'center', lineHeight: 1.7 }}>
            No queries yet.<br />Ask a question in Ask AURA and it lands here, with its generated SQL and status.
          </div>
        )}
        {(rows ?? []).map((q, i) => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 5, padding: '11px 16px', borderTop: i === 0 ? 'none' : '1px solid var(--hair)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 6, height: 6, flex: 'none', background: statusColor(q.status), borderRadius: 0 }} />
              <span style={{ fontSize: 12.5, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{q.prompt || q.sql || '(query)'}</span>
              <div style={{ flex: 1 }} />
              <span className="aw-mono" style={{ fontSize: 9, fontWeight: 700, letterSpacing: '.06em', color: statusColor(q.status) }}>{(q.status || 'unknown').toUpperCase()}</span>
              {typeof q.row_count === 'number' && <span className="aw-mono" style={{ fontSize: 10, color: 'var(--text3)' }}>{q.row_count} rows</span>}
            </div>
            {q.sql && <div className="aw-mono" style={{ fontSize: 10.5, color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--hair)', padding: '5px 9px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{q.sql}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
