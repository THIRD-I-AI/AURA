/* Connectors — native terminal-authority panel (replaces embedded classic
   AgentPanel/connections page). Shows real data sources from GET /connections
   via connectorService: registered database connections + file sources, styled
   to match the Cockpit. */
import { useCallback, useEffect, useState } from 'react';
import { connectorService } from '../../services/api';

type Connection = { id?: string; name?: string; type?: string; source_id?: string; status?: string };
type SourcesResp = { connections?: Connection[]; count?: number; file_sources?: number };

export default function ConnectorsPanel() {
  const [data, setData] = useState<SourcesResp | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = (await connectorService.listSources()) as SourcesResp;
      setData(resp);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list connectors.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const conns = data?.connections ?? [];
  const fileSources = data?.file_sources ?? 0;

  return (
    <div data-testid="wb-connectors-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {data === null ? 'loading…' : `${conns.length} database connection${conns.length === 1 ? '' : 's'} · ${fileSources} file source${fileSources === 1 ? '' : 's'}`}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={load} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '7px 14px' }}>↻ REFRESH</button>
      </div>

      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderBottom: '1px solid var(--hair)' }}>
          <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.12em', color: 'var(--text3)' }}>DATABASE CONNECTIONS</div>
        </div>
        {data === null && <div style={{ padding: '14px 16px', fontSize: 11.5, color: 'var(--text3)' }}>Loading connectors…</div>}
        {data !== null && conns.length === 0 && !error && (
          <div style={{ padding: '18px 16px', fontSize: 12, color: 'var(--text3)', lineHeight: 1.7 }}>
            No external database connections. Add PostgreSQL, MySQL, or BigQuery to query live warehouses alongside your files.
          </div>
        )}
        {conns.map((c, i) => (
          <div key={c.id || c.source_id || i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 16px', borderTop: '1px solid var(--hair)' }}>
            <span style={{ width: 6, height: 6, flex: 'none', background: c.status === 'connected' ? 'var(--accent)' : 'var(--warn)', borderRadius: 0 }} />
            <span style={{ fontSize: 12.5, color: 'var(--text)' }}>{c.name || c.source_id || '(source)'}</span>
            <div style={{ flex: 1 }} />
            <span className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.06em', color: 'var(--text3)' }}>{(c.type || 'db').toUpperCase()}</span>
          </div>
        ))}
      </div>

      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px' }}>
          <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.12em', color: 'var(--text3)' }}>FILE SOURCES</div>
          <div style={{ flex: 1 }} />
          <span className="aw-mono" style={{ fontSize: 11, color: 'var(--accent)' }}>{fileSources} active</span>
        </div>
        <div style={{ padding: '0 16px 14px', fontSize: 11.5, color: 'var(--text3)', lineHeight: 1.7 }}>
          Uploaded datasets are auto-registered as queryable sources — manage them in <span style={{ color: 'var(--text2)' }}>Files &amp; Data</span>.
        </div>
      </div>
    </div>
  );
}
