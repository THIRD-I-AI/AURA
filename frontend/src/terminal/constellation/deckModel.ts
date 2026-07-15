/**
 * Pure, DOM-free model for the constellation command deck: honest node-kind
 * tallies and connection state. Kept side-effect-free and framework-free so the
 * highest-value invariants are unit-testable in isolation (mirrors
 * terminal/pipeline/topology.ts and terminal/audit/model.ts).
 *
 * Counts are derived from the actual graph nodes/edges, NOT from
 * `graph.summary` — the summary is a server hint that drifts once uploaded
 * datasets are merged into the graph client-side, so trusting it would report
 * a number the canvas does not show.
 */
import type { LineageGraph, LineageNode } from '../../services/api';
import type { ConstellationKind } from './layout';

export interface ConstellationStats {
  total: number;
  tables: number;
  queries: number;
  dashboards: number;
  edges: number;
}

/** Inline glyphs for the deck header, one per lineage node kind. */
export const KIND_GLYPH: Record<ConstellationKind, string> = {
  table: '\u25A0',        // ■
  saved_query: '\u25C7',  // ◇
  dashboard: '\u25CE',    // ◎
};

/** Human labels (plural) for the deck header. */
export const KIND_LABEL: Record<ConstellationKind, string> = {
  table: 'tables',
  saved_query: 'queries',
  dashboard: 'dashboards',
};

/** Derive honest tallies from the real nodes/edges the canvas renders. */
export function constellationStats(graph: LineageGraph | null): ConstellationStats {
  const empty: ConstellationStats = { total: 0, tables: 0, queries: 0, dashboards: 0, edges: 0 };
  if (!graph) return empty;
  const by = (t: LineageNode['type']) => graph.nodes.filter((n) => n.type === t).length;
  return {
    total: graph.nodes.length,
    tables: by('table'),
    queries: by('saved_query'),
    dashboards: by('dashboard'),
    edges: graph.edges.length,
  };
}

/**
 * Honest connection state for the deck header. `loading` (never loaded this
 * session) is distinct from `error` (a load/poll failed) and from `live` (a
 * graph is in hand). We never show 'live' before a real graph arrives.
 */
export type ConnectionState = 'loading' | 'live' | 'error';

export const CONNECTION_GLYPH: Record<ConnectionState, string> = {
  loading: '\u25CC', // ◌ awaiting first load
  live: '\u25CF',    // ●
  error: '\u25CB',   // ○
};
