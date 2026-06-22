import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useViewport } from '../shell/ViewportProvider';
import {
  lineageService,
  subscribeWorkspace,
  type LineageGraph,
  type LineageNode,
} from '../services/api';

const NODE_WIDTH = 180;
const NODE_HEIGHT = 40;
const COL_GAP = 180;
const ROW_GAP = 16;
const PADDING = 24;

const COLUMN_ORDER: Array<LineageNode['type']> = ['table', 'saved_query', 'dashboard'];

const COLUMN_STYLE: Record<LineageNode['type'], { fill: string; stroke: string; label: string }> = {
  table:        { fill: '#1e3a5f', stroke: '#60a5fa', label: 'Tables' },
  saved_query:  { fill: '#14532d', stroke: '#34d399', label: 'Saved queries' },
  dashboard:    { fill: '#4c1d95', stroke: '#a78bfa', label: 'Dashboards' },
};

interface LaidOutNode extends LineageNode {
  x: number;
  y: number;
}

const Lineage: React.FC = () => {
  const [graph, setGraph] = useState<LineageGraph | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>('');
  const navigate = useNavigate();
  // When the context rail is present it carries the inspector, so the canvas
  // takes the full content width instead of the two-pane split.
  const { hasRail } = useViewport();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const g = await lineageService.get();
      setGraph(g);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load lineage');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => subscribeWorkspace(() => { setSelectedId(null); refresh(); }), [refresh]);

  // ── Column layout (deterministic) ──────────────────────────────
  const layout = useMemo(() => {
    if (!graph) return { nodes: [] as LaidOutNode[], width: 0, height: 0, byId: new Map<string, LaidOutNode>() };
    const f = filter.trim().toLowerCase();
    const filteredNodes = f
      ? graph.nodes.filter((n) => n.label.toLowerCase().includes(f) || n.id.toLowerCase().includes(f))
      : graph.nodes;

    const columns: Record<string, LineageNode[]> = { table: [], saved_query: [], dashboard: [] };
    for (const n of filteredNodes) columns[n.type]?.push(n);
    for (const c of Object.values(columns)) c.sort((a, b) => a.label.localeCompare(b.label));

    const laid: LaidOutNode[] = [];
    COLUMN_ORDER.forEach((type, colIdx) => {
      const colNodes = columns[type] ?? [];
      colNodes.forEach((node, rowIdx) => {
        laid.push({
          ...node,
          x: PADDING + colIdx * (NODE_WIDTH + COL_GAP),
          y: PADDING + rowIdx * (NODE_HEIGHT + ROW_GAP),
        });
      });
    });
    const maxRow = Math.max(columns.table.length, columns.saved_query.length, columns.dashboard.length, 1);
    const width = PADDING * 2 + COLUMN_ORDER.length * NODE_WIDTH + (COLUMN_ORDER.length - 1) * COL_GAP;
    const height = PADDING * 2 + maxRow * (NODE_HEIGHT + ROW_GAP);
    const byId = new Map(laid.map((n) => [n.id, n]));
    return { nodes: laid, width, height, byId };
  }, [graph, filter]);

  const visibleEdges = useMemo(() => {
    if (!graph) return [];
    return graph.edges.filter((e) => layout.byId.has(e.source) && layout.byId.has(e.target));
  }, [graph, layout.byId]);

  const selected = useMemo(() => (
    selectedId && graph ? graph.nodes.find((n) => n.id === selectedId) ?? null : null
  ), [selectedId, graph]);

  const connectedIds = useMemo(() => {
    if (!selectedId || !graph) return new Set<string>();
    const set = new Set<string>([selectedId]);
    for (const e of graph.edges) {
      if (e.source === selectedId) set.add(e.target);
      if (e.target === selectedId) set.add(e.source);
    }
    return set;
  }, [selectedId, graph]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter nodes by name…"
          aria-label="Filter lineage nodes"
          style={{
            flex: '1 1 280px', minWidth: 0,
            padding: 'var(--space-2-5) var(--space-3)',
            background: 'var(--bg-surface)', border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: 'var(--font-sm)',
          }}
        />
        <button
          onClick={refresh}
          disabled={loading}
          style={{ padding: '6px 14px', fontSize: 13, background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', cursor: loading ? 'wait' : 'pointer' }}
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
        {graph && (
          <span style={{ fontSize: 12, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
            {graph.summary.tables} tables · {graph.summary.queries} queries · {graph.summary.dashboards} dashboards · {graph.summary.edges} edges
          </span>
        )}
      </div>

      {/* On phones the columnar SVG is a wide horizontal scroll; point to the
          Constellation in the Terminal for a native pinch/pan/zoom view. Hidden
          on desktop (see .lineage-mobile-hint in design-system.css). */}
      <button
        type="button"
        className="lineage-mobile-hint"
        onClick={() => navigate('/app/terminal')}
      >
        <span aria-hidden>✦</span>
        Explore this graph interactively — open the Constellation in the Terminal
        <span aria-hidden style={{ marginLeft: 'auto' }}>→</span>
      </button>

      {error && (
        <div role="alert" style={{ padding: 12, background: 'rgba(239,68,68,0.08)', border: '1px solid #f87171', borderRadius: 8, color: '#f87171', fontSize: 13 }}>
          {error}
        </div>
      )}

      <div className={hasRail ? '' : 'aura-split aura-split--detail'}>
        {/* Graph canvas */}
        <div
          role="img"
          aria-label="Data lineage graph"
          style={{
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            background: 'var(--bg-surface)',
            overflow: 'auto',
            WebkitOverflowScrolling: 'touch',
            maxHeight: 'calc(100vh - 220px)',
          }}
        >
          {layout.nodes.length === 0 ? (
            <div style={{ padding: 48, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
              {loading ? 'Loading graph…' : 'No lineage to show yet. Save some queries or build a dashboard first.'}
            </div>
          ) : (
            <svg width={layout.width} height={layout.height} style={{ display: 'block' }}>
              {/* Column headers */}
              {COLUMN_ORDER.map((type, i) => (
                <text
                  key={type}
                  x={PADDING + i * (NODE_WIDTH + COL_GAP) + NODE_WIDTH / 2}
                  y={PADDING - 6}
                  textAnchor="middle"
                  fontSize="11"
                  fontWeight="600"
                  fill={COLUMN_STYLE[type].stroke}
                  style={{ textTransform: 'uppercase', letterSpacing: 0.5 }}
                >
                  {COLUMN_STYLE[type].label}
                </text>
              ))}

              {/* Edges */}
              {visibleEdges.map((e) => {
                const src = layout.byId.get(e.source)!;
                const tgt = layout.byId.get(e.target)!;
                const x1 = src.x + NODE_WIDTH;
                const y1 = src.y + NODE_HEIGHT / 2;
                const x2 = tgt.x;
                const y2 = tgt.y + NODE_HEIGHT / 2;
                const mid = (x1 + x2) / 2;
                const d = `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
                const highlighted = connectedIds.has(src.id) && connectedIds.has(tgt.id);
                return (
                  <path
                    key={e.id}
                    d={d}
                    stroke={highlighted ? '#fbbf24' : '#334155'}
                    strokeWidth={highlighted ? 2 : 1}
                    fill="none"
                    opacity={selectedId && !highlighted ? 0.25 : 0.85}
                  />
                );
              })}

              {/* Nodes */}
              {layout.nodes.map((n) => {
                const style = COLUMN_STYLE[n.type];
                const highlighted = !selectedId || connectedIds.has(n.id);
                return (
                  <g
                    key={n.id}
                    onClick={() => setSelectedId(n.id === selectedId ? null : n.id)}
                    style={{ cursor: 'pointer' }}
                    opacity={highlighted ? 1 : 0.35}
                  >
                    <rect
                      x={n.x} y={n.y}
                      width={NODE_WIDTH} height={NODE_HEIGHT}
                      rx={6} ry={6}
                      fill={style.fill}
                      stroke={selectedId === n.id ? '#fbbf24' : style.stroke}
                      strokeWidth={selectedId === n.id ? 2 : 1}
                    />
                    <text
                      x={n.x + 12}
                      y={n.y + NODE_HEIGHT / 2 + 4}
                      fill="#e5e7eb"
                      fontSize="12"
                      fontFamily="var(--font-sans)"
                      style={{ pointerEvents: 'none' }}
                    >
                      {n.label.length > 22 ? `${n.label.slice(0, 20)}…` : n.label}
                    </text>
                  </g>
                );
              })}
            </svg>
          )}
        </div>

        {/* Inspector — inline only when there's no context rail to host it. */}
        {!hasRail && (
          <aside style={{
            border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)',
            background: 'var(--bg-surface)', padding: 'var(--space-4)',
            position: 'sticky', top: 12, maxHeight: 'calc(100vh - 220px)', overflow: 'auto',
          }}>
            {!selected ? (
              <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>
                Click any node to inspect it.
              </div>
            ) : (
              <Inspector node={selected} graph={graph} onPick={setSelectedId} />
            )}
          </aside>
        )}
      </div>
    </div>
  );
};

const Inspector: React.FC<{ node: LineageNode; graph: LineageGraph | null; onPick: (id: string) => void }> = ({ node, graph, onPick }) => {
  const typeLabel = node.type === 'saved_query' ? 'Saved query' : node.type.charAt(0).toUpperCase() + node.type.slice(1);
  const upstream = graph?.edges.filter((e) => e.target === node.id) ?? [];
  const downstream = graph?.edges.filter((e) => e.source === node.id) ?? [];
  const byId = new Map((graph?.nodes ?? []).map((n) => [n.id, n]));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-tertiary)' }}>{typeLabel}</div>
        <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>{node.label}</div>
      </div>

      {node.type === 'saved_query' && node.metadata?.sql && (
        <pre style={{ margin: 0, padding: 8, background: 'var(--bg-sunken)', border: '1px solid var(--border-subtle)', borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 11, color: '#a5b4fc', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 160, overflow: 'auto' }}>
          {node.metadata.sql}
        </pre>
      )}

      {node.type === 'saved_query' && (
        <div style={{ display: 'flex', gap: 8, fontSize: 11, color: 'var(--text-secondary)' }}>
          {node.metadata?.starred && <span>★ starred</span>}
          {node.metadata?.scheduled && <span>⏱ scheduled</span>}
          <span>{node.metadata?.table_count ?? 0} table deps</span>
        </div>
      )}

      {node.type === 'table' && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          Referenced by {node.metadata?.referenced_by ?? 0} quer{(node.metadata?.referenced_by ?? 0) === 1 ? 'y' : 'ies'}
        </div>
      )}

      {upstream.length > 0 && (
        <NodeRefList title="Upstream" edges={upstream} byId={byId} getOther={(e) => e.source} onPick={onPick} />
      )}
      {downstream.length > 0 && (
        <NodeRefList title="Downstream" edges={downstream} byId={byId} getOther={(e) => e.target} onPick={onPick} />
      )}
    </div>
  );
};

interface NodeRefListProps {
  title: string;
  edges: Array<{ id: string; source: string; target: string }>;
  byId: Map<string, LineageNode>;
  getOther: (e: { source: string; target: string }) => string;
  onPick: (id: string) => void;
}

const NodeRefList: React.FC<NodeRefListProps> = ({ title, edges, byId, getOther, onPick }) => (
  <div>
    <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-tertiary)', marginBottom: 4 }}>{title}</div>
    <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 2 }}>
      {edges.map((e) => {
        const other = byId.get(getOther(e));
        if (!other) return null;
        return (
          <li key={e.id}>
            <button
              type="button"
              onClick={() => onPick(other.id)}
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: '2px 0', color: '#93c5fd', fontSize: 12, textAlign: 'left' }}
            >
              {other.label} <span style={{ color: 'var(--text-tertiary)', fontSize: 10 }}>({other.type.replace('_', ' ')})</span>
            </button>
          </li>
        );
      })}
    </ul>
  </div>
);

export default Lineage;
