/* Deterministic force layout for the service nodes.
   d3-force is run to completion synchronously (a fixed tick count with a
   seeded start) so the layout is stable across renders and SSR-safe — no
   animation loop, no rAF, no canvas. Motion handles the *visual* transitions;
   this only decides final coordinates. Recomputes only when the set of
   service ids changes (topology), not on every severity update. */
import { useMemo } from 'react';
import {
  forceSimulation,
  forceManyBody,
  forceCollide,
  forceRadial,
  forceCenter,
  type SimulationNodeDatum,
} from 'd3-force';
import type { RadarService } from './types';

export interface LaidOutNode extends SimulationNodeDatum {
  id: string;
  label: string;
  up: boolean | null;
  x: number;
  y: number;
}

/** Seeded pseudo-random so the layout is identical every mount (no jitter
    between renders / reloads). */
function seeded(i: number): { x: number; y: number } {
  const a = Math.sin(i * 12.9898) * 43758.5453;
  const b = Math.sin(i * 78.233) * 12345.6789;
  return { x: (a - Math.floor(a)) - 0.5, y: (b - Math.floor(b)) - 0.5 };
}

export function useRadarLayout(
  services: RadarService[],
  radius: number,
): LaidOutNode[] {
  // Topology key: only the id set matters for positions.
  const key = services.map((s) => s.id).join('|');

  return useMemo(() => {
    if (services.length === 0) return [];
    const nodes: LaidOutNode[] = services.map((s, i) => {
      const seed = seeded(i + 1);
      return {
        id: s.id,
        label: s.label,
        up: s.up,
        // Start seeded on a ring so the sim settles evenly.
        x: seed.x * radius * 1.6,
        y: seed.y * radius * 1.6,
      };
    });

    const sim = forceSimulation(nodes)
      .force('charge', forceManyBody().strength(-Math.max(120, 900 / Math.max(1, nodes.length))))
      .force('collide', forceCollide(26))
      .force('radial', forceRadial(radius, 0, 0).strength(0.85))
      .force('center', forceCenter(0, 0).strength(0.04))
      .stop();

    // Run to a stable state deterministically.
    const ticks = 220;
    for (let i = 0; i < ticks; i++) sim.tick();

    return nodes.map((n) => ({
      ...n,
      x: Number.isFinite(n.x) ? n.x! : 0,
      y: Number.isFinite(n.y) ? n.y! : 0,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, radius]);
}
