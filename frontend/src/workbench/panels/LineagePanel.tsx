/* Lineage — native terminal-authority panel (replaces embedded classic Lineage
   page). Real data-lineage graph from GET /lineage via lineageService: which
   tables feed which saved queries and dashboards. Styled to match the Cockpit. */
import { useCallback, useEffect, useState } from 'react';
import { lineageService } from '../../services/api';

type Node = { id: string; type: 'table' | 'saved_query' | 'dashboard'; label: string };
type Edge = { id: string; source: string; target: string };
type Graph = { nodes: Node[]; edges: Edge[]; summary?: { tables: number; queries: number; dashboards: number; edges: number } };

const TYPE_META: Record<Node['type'], { label: string; color: string }> = {
  table: { label: 'TABLE', color: 'var(--accent)' },
  saved_query: { label: 'QUERY', color: 'var(--warn)' },
  dashboard: { label: 'DASHBOARD', color: '#7aa2f7' },
};

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <div className="aw-panel" style={{ flex: 1, minWidth: 120, padding: '13px 16px' }}>
      <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.12em', color: 'var(--text3)' }}>{label}</div>
      <div className="aw-display" style={{ fontSize: 24, fontWeight: 600, color: 'var(--text)', marginTop: 5 }}>{value}</div>
    </div>
  );
}

export default function LineagePanel() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const g = (await lineageService.get()) as Graph;
      setGraph(g);
      setError(null);
    } catch {
      setError('Could not reach the gateway to load lineage.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const s = graph?.summary;
  const downstream = (id: string) => edges.filter((e) => e.source === id).length;

  return (
    <div data-testid="wb-lineage-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {graph === null ? 'loading…' : `${nodes.length} nodes · ${edges.length} edges · provenance graph`}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={load} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '7px 14px' }}>↻ REFRESH</button>
      </div>

      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      {s && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Tile label="TABLES" value={s.tables} />
          <Tile label="QUERIES" value={s.queries} />
          <Tile label="DASHBOARDS" value={s.dashboards} />
          <Tile label="EDGES" value={s.edges} />
        </div>
      )}

      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr 130px', padding: '10px 16px', borderBottom: '1px solid var(--hair)' }}>
          {['TYPE', 'NODE', 'DOWNSTREAM'].map((h, i) => (
            <div key={h} className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.12em', color: 'var(--text3)', textAlign: i === 2 ? 'right' : 'left' }}>{h}</div>
          ))}
        </div>
        {graph === null && <div style={{ padding: '14px 16px', fontSize: 11.5, color: 'var(--text3)' }}>Loading lineage…</div>}
        {graph !== null && nodes.length === 0 && !error && (
          <div style={{ padding: '22px 16px', fontSize: 12, color: 'var(--text3)', textAlign: 'center', lineHeight: 1.7 }}>
            No lineage yet.<br />Run queries and pin dashboards — AURA traces which datasets feed which results.
          </div>
        )}
        {nodes.map((n) => {
          const meta = TYPE_META[n.type] ?? { label: (n.type as string).toUpperCase(), color: 'var(--text3)' };
          const dn = downstream(n.id);
          return (
            <div key={n.id} className="aw-nav-item" style={{ display: 'grid', gridTemplateColumns: '110px 1fr 130px', alignItems: 'center', padding: '9px 16px', borderTop: '1px solid var(--hair)', cursor: 'default' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 6, height: 6, flex: 'none', background: meta.color, borderRadius: 0 }} />
                <span className="aw-mono" style={{ fontSize: 9, fontWeight: 600, letterSpacing: '.06em', color: meta.color }}>{meta.label}</span>
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.label}</div>
              <div className="aw-mono" style={{ fontSize: 11, color: dn ? 'var(--text2)' : 'var(--text3)', textAlign: 'right' }}>{dn ? `${dn} →` : '—'}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
