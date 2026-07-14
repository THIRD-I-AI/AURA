/**
 * AURA pipeline topology — the canonical left-to-right service DAG.
 *
 * Pure, deterministic, unit-testable (no React, no I/O). The panel renders
 * whatever this module returns; live health/telemetry is layered on top by
 * id, never baked in here. Keeping the graph static and the layout pure means
 * the "where does each service sit" story is stable across renders and the
 * positions are testable without a DOM.
 *
 * Health keys map 1:1 onto the gateway's GET /system/health `services` map:
 *   api_gateway, code_generation, database_service, execution_sandbox,
 *   scheduler, insights, metadata_store, uasr
 * Nodes whose `healthKey` is null are either not health-polled by the gateway
 * (orchestration) or are infra/client the gateway can't probe (Redpanda,
 * Redis, the browser) — they render in a neutral "unmonitored" state.
 */

export type NodeKind = 'ingress' | 'bus' | 'service' | 'sidecar' | 'store' | 'infra' | 'client';

/** Gateway /system/health service keys (null = not health-polled). */
export type HealthKey =
  | 'api_gateway'
  | 'code_generation'
  | 'database_service'
  | 'execution_sandbox'
  | 'scheduler'
  | 'insights'
  | 'metadata_store'
  | 'uasr'
  | null;

export interface PipelineNode {
  id: string;
  label: string;
  /** Column index, 0 = ingress … increasing left→right. */
  stage: number;
  /** Row within the stage (0-based, top→bottom). */
  row: number;
  kind: NodeKind;
  /** Listening port, when the node is an AURA HTTP service. */
  port?: number;
  /** Maps to /system/health `services` key; null = unmonitored/infra. */
  healthKey: HealthKey;
  /** One-line role, shown in the inspector. */
  role: string;
}

export interface PipelineEdge {
  id: string;
  source: string;
  target: string;
  /** Sidecar/observing links render dashed rather than as flow arrows. */
  kind?: 'flow' | 'sidecar' | 'cache';
}

/**
 * The service graph. Ordered by stage so the LtR reading order is obvious.
 * Stage lanes:
 *   0 Ingress   → 1 Bus → 2 Processing → 3 Store → 4 Gateway → 5 Client
 */
export const PIPELINE_NODES: PipelineNode[] = [
  // ── Stage 0: ingress ────────────────────────────────────────────────
  { id: 'connectors', label: 'Connectors', stage: 0, row: 0, kind: 'ingress', port: 8002, healthKey: 'database_service', role: 'DB + SaaS connectors (NetSuite, Workday, Kafka, any DB) — ingestion edge' },

  // ── Stage 1: streaming bus ──────────────────────────────────────────
  { id: 'redpanda', label: 'Redpanda', stage: 1, row: 0, kind: 'bus', port: 9092, healthKey: null, role: 'Kafka-compatible event bus — the pipeline backbone' },

  // ── Stage 2: processing lane ────────────────────────────────────────
  { id: 'orchestration', label: 'Orchestration', stage: 2, row: 0, kind: 'service', port: 8006, healthKey: null, role: 'Multi-agent DAG executor — drives the processing services' },
  { id: 'uasr', label: 'UASR Self-Heal', stage: 2, row: 1, kind: 'sidecar', port: 8009, healthKey: 'uasr', role: 'MAPE-K self-healing sidecar — drift detect → diagnose → repair' },
  { id: 'insights', label: 'Insights', stage: 2, row: 2, kind: 'service', port: 8005, healthKey: 'insights', role: 'Analytics + narrative insight generation' },
  { id: 'code_generation', label: 'Code Gen', stage: 2, row: 3, kind: 'service', port: 8001, healthKey: 'code_generation', role: 'LLM query/transform code synthesis' },
  { id: 'execution_sandbox', label: 'Exec Sandbox', stage: 2, row: 4, kind: 'service', port: 8003, healthKey: 'execution_sandbox', role: 'Isolated code execution runtime' },
  { id: 'scheduler', label: 'Scheduler', stage: 2, row: 5, kind: 'service', port: 8004, healthKey: 'scheduler', role: 'Cron + event-driven job scheduling' },

  // ── Stage 3: store ──────────────────────────────────────────────────
  { id: 'metadata_store', label: 'Metadata / Lineage', stage: 3, row: 0, kind: 'store', port: 8007, healthKey: 'metadata_store', role: 'Lineage graph + run metadata + audit ledger' },
  { id: 'redis', label: 'Redis', stage: 3, row: 1, kind: 'infra', port: 6379, healthKey: null, role: 'Shared state + cache + distributed repair coordination' },

  // ── Stage 4: gateway ────────────────────────────────────────────────
  { id: 'api_gateway', label: 'API Gateway', stage: 4, row: 0, kind: 'service', port: 8000, healthKey: 'api_gateway', role: 'Single entry — auth, routing, SSE fan-out, health aggregation' },

  // ── Stage 5: client ─────────────────────────────────────────────────
  { id: 'frontend', label: 'Frontend', stage: 5, row: 0, kind: 'client', healthKey: null, role: 'React cockpit — this UI' },
];

export const PIPELINE_EDGES: PipelineEdge[] = [
  { id: 'e-con-rp', source: 'connectors', target: 'redpanda', kind: 'flow' },

  { id: 'e-rp-orch', source: 'redpanda', target: 'orchestration', kind: 'flow' },
  { id: 'e-rp-uasr', source: 'redpanda', target: 'uasr', kind: 'sidecar' },
  { id: 'e-rp-ins', source: 'redpanda', target: 'insights', kind: 'flow' },

  { id: 'e-orch-cg', source: 'orchestration', target: 'code_generation', kind: 'flow' },
  { id: 'e-orch-ex', source: 'orchestration', target: 'execution_sandbox', kind: 'flow' },
  { id: 'e-orch-sch', source: 'orchestration', target: 'scheduler', kind: 'flow' },

  { id: 'e-orch-md', source: 'orchestration', target: 'metadata_store', kind: 'flow' },
  { id: 'e-ins-md', source: 'insights', target: 'metadata_store', kind: 'flow' },
  { id: 'e-uasr-md', source: 'uasr', target: 'metadata_store', kind: 'sidecar' },
  { id: 'e-cg-md', source: 'code_generation', target: 'metadata_store', kind: 'flow' },
  { id: 'e-ex-md', source: 'execution_sandbox', target: 'metadata_store', kind: 'flow' },
  { id: 'e-sch-md', source: 'scheduler', target: 'metadata_store', kind: 'flow' },

  { id: 'e-md-gw', source: 'metadata_store', target: 'api_gateway', kind: 'flow' },
  { id: 'e-redis-gw', source: 'redis', target: 'api_gateway', kind: 'cache' },

  { id: 'e-gw-fe', source: 'api_gateway', target: 'frontend', kind: 'flow' },
];

// ── Layout geometry ────────────────────────────────────────────────────
export const COL_W = 210;   // horizontal gap between stages
export const ROW_H = 92;    // vertical gap between rows within a stage
export const NODE_W = 150;
export const NODE_H = 56;
export const PAD_X = 40;
export const PAD_Y = 40;

export interface PositionedNode extends PipelineNode {
  x: number;
  y: number;
}

export interface PositionedEdge extends PipelineEdge {
  x1: number; y1: number;   // source anchor (right edge center)
  x2: number; y2: number;   // target anchor (left edge center)
}

export interface PipelineLayout {
  nodes: PositionedNode[];
  edges: PositionedEdge[];
  width: number;
  height: number;
}

/**
 * Deterministic LtR layout. Each stage is a column; rows stack vertically and
 * are centered so short stages sit in the middle of the tallest one. Pure —
 * same input always yields the same coordinates.
 */
export function computePipelineLayout(
  nodes: PipelineNode[] = PIPELINE_NODES,
  edges: PipelineEdge[] = PIPELINE_EDGES,
): PipelineLayout {
  const stages = new Map<number, PipelineNode[]>();
  for (const n of nodes) {
    const arr = stages.get(n.stage) ?? [];
    arr.push(n);
    stages.set(n.stage, arr);
  }

  const maxStage = Math.max(0, ...nodes.map((n) => n.stage));
  const maxRows = Math.max(1, ...[...stages.values()].map((a) => a.length));
  const colHeight = maxRows * ROW_H;

  const posById = new Map<string, PositionedNode>();
  for (const [stage, arr] of stages) {
    const sorted = [...arr].sort((a, b) => a.row - b.row);
    const stageHeight = sorted.length * ROW_H;
    const yOffset = (colHeight - stageHeight) / 2; // vertical centering
    sorted.forEach((n, i) => {
      const x = PAD_X + stage * COL_W;
      const y = PAD_Y + yOffset + i * ROW_H;
      posById.set(n.id, { ...n, x, y });
    });
  }

  const positioned: PositionedNode[] = nodes.map((n) => posById.get(n.id)!);

  const positionedEdges: PositionedEdge[] = edges.map((e) => {
    const s = posById.get(e.source)!;
    const t = posById.get(e.target)!;
    return {
      ...e,
      x1: s.x + NODE_W, y1: s.y + NODE_H / 2,
      x2: t.x, y2: t.y + NODE_H / 2,
    };
  });

  const width = PAD_X * 2 + maxStage * COL_W + NODE_W;
  const height = PAD_Y * 2 + colHeight;

  return { nodes: positioned, edges: positionedEdges, width, height };
}

/** Health status a node can be in, derived from telemetry. */
export type NodeStatus = 'healthy' | 'degraded' | 'down' | 'unknown' | 'unmonitored';

export const STATUS_GLYPH: Record<NodeStatus, string> = {
  healthy: '●',
  degraded: '◐',
  down: '○',
  unknown: '◌',
  unmonitored: '·',
};
