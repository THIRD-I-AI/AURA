import { describe, it, expect } from 'vitest';
import { PIPELINE_NODES, STATUS_GLYPH, type NodeStatus } from '../topology';
import { nodeStatus } from '../usePipelineTelemetry';

/**
 * Status-honesty invariants.
 *
 * These encode the contract that the in-browser pass caught being violated:
 * a monitored-but-offline service (healthKey present, no reading yet) was
 * showing 'unmonitored' while the inspector said monitored:yes — a self
 * contradiction. The distinct 'unknown' state fixes it. A unit test that
 * asserts the invariant (not a snapshot) is what keeps it fixed.
 */
describe('nodeStatus honesty', () => {
  const monitored = PIPELINE_NODES.filter((n) => n.healthKey);
  const unmonitored = PIPELINE_NODES.filter((n) => !n.healthKey);

  it('has at least one monitored and one unmonitored node (fixture sanity)', () => {
    expect(monitored.length).toBeGreaterThan(0);
    expect(unmonitored.length).toBeGreaterThan(0);
  });

  it('a monitored node with NO reading is "unknown", never "unmonitored"', () => {
    const empty: Record<string, NodeStatus> = {};
    for (const n of monitored) {
      const s = nodeStatus(n.healthKey, empty);
      expect(s, `${n.id} (healthKey=${n.healthKey})`).toBe('unknown');
      expect(s).not.toBe('unmonitored');
    }
  });

  it('a node with NO healthKey is genuinely "unmonitored"', () => {
    for (const n of unmonitored) {
      expect(nodeStatus(n.healthKey, {}), n.id).toBe('unmonitored');
    }
  });

  it('a monitored node returns its live reading verbatim when present', () => {
    for (const reading of ['healthy', 'degraded', 'down', 'unknown'] as NodeStatus[]) {
      const map = Object.fromEntries(
        monitored.map((n) => [n.healthKey as string, reading]),
      ) as Record<string, NodeStatus>;
      for (const n of monitored) {
        expect(nodeStatus(n.healthKey, map), `${n.id}=${reading}`).toBe(reading);
      }
    }
  });

  it('every status has a distinct glyph (no two states look alike)', () => {
    const glyphs = Object.values(STATUS_GLYPH);
    expect(new Set(glyphs).size).toBe(glyphs.length);
    // the two easily-confused states must not share a glyph
    expect(STATUS_GLYPH.unknown).not.toBe(STATUS_GLYPH.unmonitored);
  });
});
