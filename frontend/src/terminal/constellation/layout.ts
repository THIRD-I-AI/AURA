/**
 * Constellation layout — pure, deterministic graph → React Flow transform.
 * d3-force settles node positions synchronously (no animation loop here), so the
 * heavy physics stays out of render and is unit-testable.
 */
import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceCenter,
  forceCollide,
  type SimulationNodeDatum,
} from 'd3-force';
import type { Node, Edge } from '@xyflow/react';
import type { LineageGraph, LineageNode } from '../../services/api';

export type ConstellationKind = LineageNode['type'];

export interface AuraNodeData extends Record<string, unknown> {
  label: string;
  kind: ConstellationKind;
  degree: number;
}

export type RFAuraNode = Node<AuraNodeData, 'aura'>;

interface SimNode extends SimulationNodeDatum {
  id: string;
}

/** The node id + its 1-hop neighbors (for the hover/click focus effect). */
export function neighborSet(edges: LineageGraph['edges'], id: string): Set<string> {
  const s = new Set<string>([id]);
  for (const e of edges) {
    if (e.source === id) s.add(e.target);
    else if (e.target === id) s.add(e.source);
  }
  return s;
}

/** Lineage graph → positioned React Flow nodes/edges. Deterministic. */
export function computeConstellationLayout(
  graph: LineageGraph,
): { nodes: RFAuraNode[]; edges: Edge[] } {
  if (!graph.nodes.length) return { nodes: [], edges: [] };

  const degree = new Map<string, number>();
  for (const e of graph.edges) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
  }

  const simNodes: SimNode[] = graph.nodes.map((n) => ({ id: n.id }));
  const simLinks = graph.edges.map((e) => ({ source: e.source, target: e.target }));

  forceSimulation(simNodes)
    .force('charge', forceManyBody().strength(-340))
    .force('link', forceLink(simLinks).id((d) => (d as SimNode).id).distance(130))
    .force('center', forceCenter(0, 0))
    .force('collide', forceCollide(52))
    .stop()
    .tick(320); // settle synchronously — deterministic, no async loop

  const byId = new Map(simNodes.map((n) => [n.id, n]));

  const nodes: RFAuraNode[] = graph.nodes.map((n) => {
    const p = byId.get(n.id);
    return {
      id: n.id,
      type: 'aura',
      position: { x: p?.x ?? 0, y: p?.y ?? 0 },
      data: { label: n.label, kind: n.type, degree: degree.get(n.id) ?? 0 },
    };
  });

  const edges: Edge[] = graph.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    animated: true,
  }));

  return { nodes, edges };
}
