import { describe, expect, it } from 'vitest';
import { computeConstellationLayout, neighborSet } from '../layout';
import type { LineageGraph } from '../../../services/api';

const graph: LineageGraph = {
  success: true,
  nodes: [
    { id: 't1', type: 'table', label: 'sales', metadata: {} },
    { id: 'q1', type: 'saved_query', label: 'rev_by_region', metadata: {} },
    { id: 'd1', type: 'dashboard', label: 'exec', metadata: {} },
  ],
  edges: [
    { id: 'e1', source: 't1', target: 'q1' },
    { id: 'e2', source: 'q1', target: 'd1' },
  ],
  summary: { tables: 1, queries: 1, dashboards: 1, edges: 2 },
};

describe('computeConstellationLayout', () => {
  it('maps each lineage node to a positioned RF node with kind + degree', () => {
    const { nodes } = computeConstellationLayout(graph);
    expect(nodes).toHaveLength(3);
    const t1 = nodes.find((n) => n.id === 't1')!;
    expect(Number.isFinite(t1.position.x)).toBe(true);
    expect(Number.isFinite(t1.position.y)).toBe(true);
    expect(t1.type).toBe('aura');
    expect(t1.data.kind).toBe('table');
    expect(t1.data.degree).toBe(1);
    expect(nodes.find((n) => n.id === 'q1')!.data.degree).toBe(2);
  });

  it('maps each lineage edge to an animated RF edge', () => {
    const { edges } = computeConstellationLayout(graph);
    expect(edges).toHaveLength(2);
    expect(edges.every((e) => e.animated)).toBe(true);
    expect(edges[0]).toMatchObject({ id: 'e1', source: 't1', target: 'q1' });
  });

  it('handles an empty graph', () => {
    const { nodes, edges } = computeConstellationLayout({
      success: true, nodes: [], edges: [],
      summary: { tables: 0, queries: 0, dashboards: 0, edges: 0 },
    });
    expect(nodes).toEqual([]);
    expect(edges).toEqual([]);
  });
});

describe('neighborSet', () => {
  it('returns the node id and its 1-hop neighbors', () => {
    expect(neighborSet(graph.edges, 'q1')).toEqual(new Set(['q1', 't1', 'd1']));
    expect(neighborSet(graph.edges, 't1')).toEqual(new Set(['t1', 'q1']));
  });
});
