import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  MiniMap,
  Controls,
  useReactFlow,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { IDockviewPanelProps } from 'dockview-react';
import { lineageService, uploadService, type LineageGraph, type LineageNode } from '../../services/api';
import { useCockpit } from '../CockpitProvider';
import { AuraNode } from '../constellation/AuraNode';
import { computeConstellationLayout, neighborSet, type RFAuraNode, type ConstellationKind } from '../constellation/layout';
import {
  constellationStats,
  KIND_GLYPH,
  KIND_LABEL,
  CONNECTION_GLYPH,
  type ConnectionState,
  type ConstellationStats,
} from '../constellation/deckModel';

const nodeTypes = { aura: AuraNode };

const stem = (name: string) => name.replace(/\.[^.]+$/, '');

/**
 * Merge the tenant's uploaded data files into the lineage graph as `table`
 * nodes, so the constellation reflects the user's actual datasets even before
 * any query/dashboard lineage exists. Dedupes against lineage tables by stem
 * (a "customer" lineage node and a "customer.csv" upload are the same thing).
 */
function withDatasets(
  lineage: LineageGraph,
  files: Array<{ filename: string; size: number }>,
): LineageGraph {
  const lineageTableStems = new Set(
    lineage.nodes.filter((n) => n.type === 'table').map((n) => stem(n.label).toLowerCase()),
  );
  const datasetNodes: LineageNode[] = files
    .filter((f) => !lineageTableStems.has(stem(f.filename).toLowerCase()))
    .map((f) => ({
      id: `file:${f.filename}`,
      type: 'table' as const,
      label: f.filename,
      metadata: { source: 'upload', size: f.size },
    }));
  if (!datasetNodes.length) return lineage;
  return {
    ...lineage,
    nodes: [...datasetNodes, ...lineage.nodes],
    summary: { ...lineage.summary, tables: lineage.summary.tables + datasetNodes.length },
  };
}

/** Inner canvas — runs inside a ReactFlowProvider so it can use the flow API. */
function Canvas({ graph, fitSignal }: { graph: LineageGraph; fitSignal: number }) {
  const { activeDataset, setActiveDataset } = useCockpit();
  const { setCenter, fitView } = useReactFlow();
  const base = useMemo(() => computeConstellationLayout(graph), [graph]);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [search, setSearch] = useState('');

  // Bidirectional cross-filter: when a dataset is selected elsewhere (the
  // Datasets panel, the chat), focus + centre its node here too.
  useEffect(() => {
    if (!activeDataset) return;
    const needle = stem(activeDataset).toLowerCase();
    const hit = base.nodes.find(
      (n) => n.data.label.toLowerCase() === activeDataset.toLowerCase()
        || stem(n.data.label).toLowerCase() === needle,
    );
    if (hit) {
      setFocusId(hit.id);
      setCenter(hit.position.x, hit.position.y, { zoom: 1.3, duration: 500 });
    }
  }, [activeDataset, base.nodes, setCenter]);

  // Fit-to-view control from the deck header (signal counter avoids a ref).
  useEffect(() => {
    if (fitSignal > 0) fitView({ duration: 500, padding: 0.2 });
  }, [fitSignal, fitView]);

  const focus = useMemo(
    () => (focusId ? neighborSet(graph.edges, focusId) : null),
    [focusId, graph.edges],
  );

  // Palantir focus: the selected node + its neighbors light up, the rest dim.
  const nodes: Node[] = useMemo(
    () => base.nodes.map((n) => ({
      ...n,
      className: focus ? (focus.has(n.id) ? 'is-focused' : 'is-dimmed') : '',
    })),
    [base.nodes, focus],
  );
  const edges: Edge[] = useMemo(
    () => base.edges.map((e) => ({
      ...e,
      className: focus ? (focus.has(e.source) && focus.has(e.target) ? 'is-focused' : 'is-dimmed') : '',
    })),
    [base.edges, focus],
  );

  const onNodeClick: NodeMouseHandler = useCallback(
    (_evt, node) => {
      setFocusId(node.id);
      const data = (node as RFAuraNode).data;
      // clicking a dataset node cross-filters the cockpit (S46 activeDataset bus)
      if (data?.kind === 'table') setActiveDataset(data.label);
    },
    [setActiveDataset],
  );

  const runSearch = useCallback(() => {
    const q = search.trim().toLowerCase();
    if (!q) return;
    const hit = base.nodes.find((n) => n.data.label.toLowerCase().includes(q));
    if (hit) {
      setFocusId(hit.id);
      setCenter(hit.position.x, hit.position.y, { zoom: 1.4, duration: 600 });
    }
  }, [search, base.nodes, setCenter]);

  return (
    <div className="constellation-canvas">
      <div className="constellation-search">
        <input
          data-testid="constellation-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') runSearch(); }}
          placeholder="Find a node…"
        />
        {focusId && (
          <button data-testid="constellation-clear" onClick={() => setFocusId(null)}>clear focus</button>
        )}
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onPaneClick={() => setFocusId(null)}
        fitView
        minZoom={0.2}
        maxZoom={2.5}
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        elementsSelectable
      >
        <Background gap={22} size={1} />
        <MiniMap pannable zoomable nodeColor={() => 'var(--t-live)'} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

/** Command-deck header: honest live node/edge tallies + connection state. */
function ConstellationDeckHead({
  stats,
  connection,
  lastUpdate,
  onRefresh,
  onFit,
  busy,
}: {
  stats: ConstellationStats;
  connection: ConnectionState;
  lastUpdate: string | null;
  onRefresh: () => void;
  onFit: () => void;
  busy: boolean;
}) {
  const cells: Array<{ k: ConstellationKind; n: number }> = [
    { k: 'table', n: stats.tables },
    { k: 'saved_query', n: stats.queries },
    { k: 'dashboard', n: stats.dashboards },
  ];
  const shown = cells.filter((c) => c.n !== 0);
  return (
    <div className="cst-head">
      <div className="cst-head-title">
        <span className="cst-head-glyph">✦</span>
        <span>Constellation</span>
      </div>
      <div className={`cst-conn cst-conn-${connection}`}>
        <span className="cst-conn-glyph">{CONNECTION_GLYPH[connection]}</span>
        <span>{connection}</span>
      </div>
      <div className="cst-head-counts">
        <span className="cst-count cst-count-total">{stats.total} nodes</span>
        {shown.map((c) => (
          <span key={c.k} className={`cst-count cst-count-${c.k}`}>
            <span className="cst-count-glyph">{KIND_GLYPH[c.k]}</span>
            {c.n} {KIND_LABEL[c.k]}
          </span>
        ))}
        <span className="cst-count cst-count-edges">{stats.edges} edges</span>
      </div>
      <span className="cst-head-spacer" />
      <div className="cst-actions">
        <button className="cst-btn" onClick={onFit} title="Fit graph to view">fit</button>
        <button className="cst-btn" onClick={onRefresh} disabled={busy} title="Refresh lineage now">
          {busy ? '…' : '↻'}
        </button>
      </div>
      {lastUpdate ? (
        <span className="cst-head-ts">{new Date(lastUpdate).toLocaleTimeString()}</span>
      ) : null}
    </div>
  );
}

export default function ConstellationPanel(_props: IDockviewPanelProps) {
  const [graph, setGraph] = useState<LineageGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);
  const [conn, setConn] = useState<ConnectionState>('loading');
  const [fitSignal, setFitSignal] = useState(0);
  const [busy, setBusy] = useState(false);
  const filesKeyRef = useRef<string>('');

  useEffect(() => {
    let alive = true;

    const build = async () => {
      const [lineage, files] = await Promise.all([
        lineageService.get(),
        uploadService.getUploadedFiles().catch(() => []),
      ]);
      if (!alive) return;
      filesKeyRef.current = files.map((f) => f.filename).sort().join('|');
      setGraph(withDatasets(lineage, files));
      setLastUpdate(new Date().toISOString());
      setConn('live');
    };

    build().catch((e) => {
      if (alive) {
        setError(e instanceof Error ? e.message : 'Failed to load lineage');
        setConn('error');
      }
    });

    // Auto-populate newly uploaded datasets: poll the (cheap) file list and
    // rebuild the graph only when the set actually changes, to avoid re-layout
    // churn on every tick.
    const iv = setInterval(async () => {
      try {
        const files = await uploadService.getUploadedFiles();
        const key = files.map((f) => f.filename).sort().join('|');
        if (!alive || key === filesKeyRef.current) return;
        filesKeyRef.current = key;
        const lineage = await lineageService.get();
        if (alive) { setGraph(withDatasets(lineage, files)); setLastUpdate(new Date().toISOString()); setConn('live'); }
      } catch {
        /* transient — keep the current graph */
      }
    }, 8000);

    return () => { alive = false; clearInterval(iv); };
  }, []);

  const refresh = useCallback(() => {
    setBusy(true);
    void (async () => {
      try {
        const [lineage, files] = await Promise.all([
          lineageService.get(),
          uploadService.getUploadedFiles().catch(() => []),
        ]);
        filesKeyRef.current = files.map((f) => f.filename).sort().join('|');
        setGraph(withDatasets(lineage, files));
        setLastUpdate(new Date().toISOString());
        setConn('live');
        setError(null);
      } catch (e) {
        setConn('error');
        setError(e instanceof Error ? e.message : 'Failed to load lineage');
      } finally {
        setBusy(false);
      }
    })();
  }, []);
  const fit = useCallback(() => setFitSignal((x) => x + 1), []);

  const shell = (inner: React.ReactNode) => (
    <div data-testid="constellation-panel" className="aura-panel constellation-panel">{inner}</div>
  );

  const stats = constellationStats(graph);
  let body: React.ReactNode;
  if (error && !graph) {
    body = <div className="panel-error-inline">{error}</div>;
  } else if (!graph) {
    body = <div className="panel-loading">Mapping the constellation…</div>;
  } else if (!graph.nodes.length) {
    body = (
      <div className="constellation-empty">
        No datasets yet — upload a file (or run queries) to populate the graph.
      </div>
    );
  } else {
    body = (
      <ReactFlowProvider>
        <Canvas graph={graph} fitSignal={fitSignal} />
      </ReactFlowProvider>
    );
  }

  return shell(
    <div className="cst-deck">
      <ConstellationDeckHead
        stats={stats}
        connection={conn}
        lastUpdate={lastUpdate}
        onRefresh={refresh}
        onFit={fit}
        busy={busy}
      />
      <div className="cst-canvas-wrap">{body}</div>
    </div>,
  );
}
