import { describe, it, expect } from 'vitest';
import {
  PIPELINE_NODES,
  PIPELINE_EDGES,
  computePipelineLayout,
  STATUS_GLYPH,
  COL_W,
  PAD_X,
  NODE_W,
  type NodeStatus,
} from '../topology';

describe('pipeline topology', () => {
  it('every edge references existing nodes', () => {
    const ids = new Set(PIPELINE_NODES.map((n) => n.id));
    for (const e of PIPELINE_EDGES) {
      expect(ids.has(e.source), `edge ${e.id} source ${e.source}`).toBe(true);
      expect(ids.has(e.target), `edge ${e.id} target ${e.target}`).toBe(true);
    }
  });

  it('node ids are unique', () => {
    const ids = PIPELINE_NODES.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('edges flow strictly left-to-right (source stage <= target stage)', () => {
    const stageOf = new Map(PIPELINE_NODES.map((n) => [n.id, n.stage]));
    for (const e of PIPELINE_EDGES) {
      expect(stageOf.get(e.source)!).toBeLessThanOrEqual(stageOf.get(e.target)!);
    }
  });

  it('health keys are unique among monitored nodes and cover the 8 gateway services', () => {
    const keys = PIPELINE_NODES.map((n) => n.healthKey).filter((k): k is NonNullable<typeof k> => k != null);
    expect(new Set(keys).size).toBe(keys.length); // no duplicate mapping
    expect(new Set(keys)).toEqual(
      new Set([
        'api_gateway',
        'code_generation',
        'database_service',
        'execution_sandbox',
        'scheduler',
        'insights',
        'metadata_store',
        'uasr',
      ]),
    );
  });

  it('layout is deterministic and positions every node + edge', () => {
    const a = computePipelineLayout();
    const b = computePipelineLayout();
    expect(a).toEqual(b);
    expect(a.nodes).toHaveLength(PIPELINE_NODES.length);
    expect(a.edges).toHaveLength(PIPELINE_EDGES.length);
    expect(a.width).toBeGreaterThan(0);
    expect(a.height).toBeGreaterThan(0);
  });

  it('x increases with stage; column spacing is COL_W', () => {
    const { nodes } = computePipelineLayout();
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const s0 = byId.get('connectors')!; // stage 0
    const s1 = byId.get('redpanda')!; // stage 1
    expect(s0.x).toBe(PAD_X);
    expect(s1.x - s0.x).toBe(COL_W);
    expect(byId.get('frontend')!.x).toBeGreaterThan(byId.get('api_gateway')!.x);
  });

  it('edge anchors sit on node right/left edges', () => {
    const { nodes, edges } = computePipelineLayout();
    const byId = new Map(nodes.map((n) => [n.id, n]));
    for (const e of edges) {
      const s = byId.get(e.source)!;
      expect(e.x1).toBe(s.x + NODE_W); // leaves source right edge
      const t = byId.get(e.target)!;
      expect(e.x2).toBe(t.x); // enters target left edge
    }
  });

  it('exposes a glyph for every status', () => {
    const statuses: NodeStatus[] = ['healthy', 'degraded', 'down', 'unmonitored'];
    for (const s of statuses) {
      expect(typeof STATUS_GLYPH[s]).toBe('string');
      expect(STATUS_GLYPH[s].length).toBeGreaterThan(0);
    }
  });
});
