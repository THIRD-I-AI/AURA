/* Dashboards — native terminal-authority panel (replaces embedded classic
   Dashboards page). Real saved dashboards from GET /dashboards via
   dashboardService, styled to match the Cockpit. */
import { useCallback, useEffect, useState } from 'react';
import { dashboardService } from '../../services/api';

type Tile = { id?: string };
type Dashboard = { id: string; name?: string; description?: string | null; tiles?: Tile[]; updated_at?: string };

export default function DashboardsPanel() {
  const [items, setItems] = useState<Dashboard[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await dashboardService.list();
      setItems((list ?? []) as Dashboard[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to load dashboards.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const count = items?.length ?? 0;

  return (
    <div data-testid="wb-dashboards-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {items === null ? 'loading…' : `${count} dashboard${count === 1 ? '' : 's'} · this workspace`}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={load} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '7px 14px' }}>↻ REFRESH</button>
      </div>

      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      {items !== null && count === 0 && !error ? (
        <div className="aw-panel" style={{ padding: '26px 16px', fontSize: 12, color: 'var(--text3)', textAlign: 'center', lineHeight: 1.7 }}>
          No dashboards yet.<br />Pin a query result from Ask AURA to build a live dashboard of your workspace metrics.
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(260px, 100%), 1fr))', gap: 12 }}>
          {items === null && <div className="aw-panel" style={{ padding: 16, fontSize: 11.5, color: 'var(--text3)' }}>Loading…</div>}
          {(items ?? []).map((d) => (
            <div key={d.id} className="aw-panel aw-hover-accent-bd" style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 6, cursor: 'pointer' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 6, height: 6, flex: 'none', background: 'var(--accent)', borderRadius: 0 }} />
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name || '(untitled)'}</span>
              </div>
              {d.description && <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.5 }}>{d.description}</div>}
              <div className="aw-mono" style={{ fontSize: 9.5, color: 'var(--text3)', marginTop: 2 }}>{(d.tiles?.length ?? 0)} tile{(d.tiles?.length ?? 0) === 1 ? '' : 's'}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
