import { useCallback, useEffect, useMemo, useState } from 'react';
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
import { lineageService, type LineageGraph } from '../../services/api';
import { useCockpit } from '../CockpitProvider';
import { AuraNode } from '../constellation/AuraNode';
import { computeConstellationLayout, neighborSet, type RFAuraNode } from '../constellation/layout';

const nodeTypes = { aura: AuraNode };

/** Inner canvas — runs inside a ReactFlowProvider so it can use the flow API. */
function Canvas({ graph }: { graph: LineageGraph }) {
  const { setActiveDataset } = useCockpit();
  const { setCenter } = useReactFlow();
  const base = useMemo(() => computeConstellationLayout(graph), [graph]);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [search, setSearch] = useState('');

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

export default function ConstellationPanel(_props: IDockviewPanelProps) {
  const [graph, setGraph] = useState<LineageGraph | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    lineageService.get()
      .then((g) => { if (alive) setGraph(g); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : 'Failed to load lineage'); });
    return () => { alive = false; };
  }, []);

  const shell = (inner: React.ReactNode) => (
    <div data-testid="constellation-panel" className="aura-panel constellation-panel">{inner}</div>
  );

  if (error) return shell(<div className="panel-error-inline">{error}</div>);
  if (!graph) return shell(<div className="panel-loading">Mapping the constellation…</div>);
  if (!graph.nodes.length) {
    return shell(
      <div className="constellation-empty">
        No lineage yet — run queries and build dashboards to populate the graph.
      </div>,
    );
  }

  return shell(
    <ReactFlowProvider>
      <Canvas graph={graph} />
    </ReactFlowProvider>,
  );
}
