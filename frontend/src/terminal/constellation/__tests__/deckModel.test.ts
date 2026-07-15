import { describe, expect, it } from 'vitest';
import type { LineageGraph } from '../../../services/api';
import {
  constellationStats,
  KIND_GLYPH,
  KIND_LABEL,
  CONNECTION_GLYPH,
} from '../deckModel';

const GRAPH: LineageGraph = {
  success: true,
  nodes: [
    { id: 't1', type: 'table', label: 'sales', metadata: {} },
    { id: 't2', type: 'table', label: 'customer', metadata: {} },
    { id: 'q1', type: 'saved_query', label: 'rev', metadata: {} },
    { id: 'd1', type: 'dashboard', label: 'exec', metadata: {} },
  ],
  edges: [
    { id: 'e1', source: 't1', target: 'q1' },
    { id: 'e2', source: 'q1', target: 'd1' },
  ],
  // Deliberately WRONG summary — stats must ignore it and count real nodes.
  summary: { tables: 99, queries: 99, dashboards: 99, edges: 99 },
};

describe('constellation deck model — stats', () => {
  it('counts real nodes/edges, not the (drift-prone) server summary', () => {
    const s = constellationStats(GRAPH);
    expect(s.total).toBe(4);
    expect(s.tables).toBe(2);
    expect(s.queries).toBe(1);
    expect(s.dashboards).toBe(1);
    expect(s.edges).toBe(2);
  });

  it('kind tallies sum to the total', () => {
    const s = constellationStats(GRAPH);
    expect(s.tables + s.queries + s.dashboards).toBe(s.total);
  });

  it('returns all-zero for a null graph (never loaded yet)', () => {
    const s = constellationStats(null);
    expect(s).toEqual({ total: 0, tables: 0, queries: 0, dashboards: 0, edges: 0 });
  });
});

describe('constellation deck model — glyph tables', () => {
  it('has a distinct glyph and a label for every node kind', () => {
    const kinds = ['table', 'saved_query', 'dashboard'] as const;
    const glyphs = kinds.map((k) => KIND_GLYPH[k]);
    for (const k of kinds) {
      expect(KIND_GLYPH[k]).toBeTruthy();
      expect(KIND_LABEL[k]).toBeTruthy();
    }
    expect(new Set(glyphs).size).toBe(kinds.length);
  });

  it('has a distinct glyph for each connection state (loading != live != error)', () => {
    const g = [CONNECTION_GLYPH.loading, CONNECTION_GLYPH.live, CONNECTION_GLYPH.error];
    for (const x of g) expect(x).toBeTruthy();
    expect(new Set(g).size).toBe(3);
  });
});
