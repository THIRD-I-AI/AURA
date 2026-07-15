/**
 * usePipelineTelemetry — the single live-state source for the pipeline panel.
 *
 * Merges three real backend channels into one view:
 *   1. REST bootstrap  GET /system/health         → per-service status snapshot
 *   2. SSE  system:health                          → status deltas (no poll)
 *   3. SSE  uasr:metrics                           → Hᵤ score + recovery signal
 *   4. REST streamingService.list()                → running stream pipelines
 *
 * It also keeps a bounded event ring so the panel can render a replayable
 * timeline (scrub back through recent events). Nothing here fabricates data:
 * when the backend is unreachable every node reports its declared status and
 * the log rail simply stays quiet.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSSE, type SSEEvent } from '../../hooks/useSSE';
import { systemService, streamingService, type StreamPipelineDef } from '../../services/api';
import type { HealthKey, NodeStatus } from './topology';

export interface TimelineEvent {
  seq: number;
  ts: string;
  topic: string;
  type: string;
  /** Short human line for the log rail. */
  line: string;
  payload: unknown;
}

export interface PipelineTelemetry {
  /** healthKey → status, from /system/health + SSE. */
  serviceStatus: Record<string, NodeStatus>;
  overall: 'healthy' | 'degraded' | 'critical' | 'unknown';
  healthyServices: number;
  totalServices: number;
  huScore: number | null;
  /** Running stream pipelines keyed by id. */
  pipelines: StreamPipelineDef[];
  /** Bounded, newest-last event ring for the timeline. */
  events: TimelineEvent[];
  connected: boolean;
  lastUpdate: string | null;
}

const EVENT_LIMIT = 500;

function statusFrom(raw: unknown): NodeStatus {
  if (raw === 'healthy') return 'healthy';
  if (raw === 'degraded') return 'degraded';
  if (raw === 'down') return 'down';
  // Present in /system/health = a monitored service; an unrecognised value
  // means we simply don't know yet — not that it's unmonitored.
  return 'unknown';
}

export function usePipelineTelemetry(pollMs = 8000): PipelineTelemetry {
  const [serviceStatus, setServiceStatus] = useState<Record<string, NodeStatus>>({});
  const [overall, setOverall] = useState<PipelineTelemetry['overall']>('unknown');
  const [healthyServices, setHealthyServices] = useState(0);
  const [totalServices, setTotalServices] = useState(0);
  const [huScore, setHuScore] = useState<number | null>(null);
  const [pipelines, setPipelines] = useState<StreamPipelineDef[]>([]);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);

  const seqRef = useRef(0);

  const pushEvent = useCallback((topic: string, type: string, line: string, payload: unknown) => {
    seqRef.current += 1;
    const ev: TimelineEvent = {
      seq: seqRef.current,
      ts: new Date().toISOString(),
      topic,
      type,
      line,
      payload,
    };
    setEvents((prev) => {
      const next = [...prev, ev];
      return next.length > EVENT_LIMIT ? next.slice(next.length - EVENT_LIMIT) : next;
    });
  }, []);

  const applyHealth = useCallback((data: {
    services?: Record<string, { status?: string }>;
    overall?: string;
    healthy_services?: number;
    total_services?: number;
    hu_score?: number | null;
  }) => {
    if (data.services) {
      const next: Record<string, NodeStatus> = {};
      for (const [k, v] of Object.entries(data.services)) {
        next[k] = statusFrom(v?.status);
      }
      setServiceStatus(next);
    }
    if (typeof data.overall === 'string') {
      setOverall(data.overall as PipelineTelemetry['overall']);
    }
    if (typeof data.healthy_services === 'number') setHealthyServices(data.healthy_services);
    if (typeof data.total_services === 'number') setTotalServices(data.total_services);
    if (data.hu_score != null) setHuScore(data.hu_score);
    setLastUpdate(new Date().toISOString());
  }, []);

  // ── 1. REST bootstrap + periodic reconcile ─────────────────────────
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const data = await systemService.health();
        if (!alive) return;
        applyHealth(data);
      } catch {
        /* offline — leave nodes at declared status, wait for SSE */
      }
    };
    load();
    const t = setInterval(load, pollMs);
    return () => { alive = false; clearInterval(t); };
  }, [applyHealth, pollMs]);

  // ── streaming pipeline list (reconciled on the same cadence) ────────
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await streamingService.list();
        if (!alive) return;
        setPipelines(res.pipelines ?? []);
      } catch {
        /* streaming module optional — ignore */
      }
    };
    load();
    const t = setInterval(load, pollMs);
    return () => { alive = false; clearInterval(t); };
  }, [pollMs]);

  // ── 2. SSE system:health ────────────────────────────────────────────
  const onHealth = useCallback((e: SSEEvent) => {
    const p = e.payload as Record<string, unknown>;
    applyHealth(p as Parameters<typeof applyHealth>[0]);
    const healthy = p?.healthy_services ?? '?';
    const total = p?.total_services ?? '?';
    pushEvent(e.topic, e.type, `health · ${healthy}/${total} services · ${String(p?.overall ?? '')}`, p);
  }, [applyHealth, pushEvent]);
  const { connected } = useSSE({ topic: 'system:health', onEvent: onHealth });

  // ── 3. SSE uasr:metrics ─────────────────────────────────────────────
  const onUasr = useCallback((e: SSEEvent) => {
    const p = e.payload as Record<string, unknown>;
    const hu = typeof p?.hu_score === 'number' ? p.hu_score : null;
    if (hu != null) setHuScore(hu);
    const parts: string[] = [];
    if (hu != null) parts.push(`Hᵤ=${hu.toFixed(3)}`);
    if (p?.drift_detected) parts.push('drift');
    if (p?.recovery_pending != null) parts.push(`pending=${p.recovery_pending}`);
    pushEvent(e.topic, e.type, `uasr · ${parts.join(' · ') || 'metrics'}`, p);
  }, [pushEvent]);
  useSSE({ topic: 'uasr:metrics', onEvent: onUasr });

  return useMemo(() => ({
    serviceStatus,
    overall,
    healthyServices,
    totalServices,
    huScore,
    pipelines,
    events,
    connected,
    lastUpdate,
  }), [serviceStatus, overall, healthyServices, totalServices, huScore, pipelines, events, connected, lastUpdate]);
}

/**
 * Resolve a node's live status from its healthKey. No healthKey → the node is
 * genuinely unmonitored (infra/client). A healthKey with no reading yet →
 * 'unknown' (monitored, awaiting data) — never conflate the two.
 */
export function nodeStatus(healthKey: HealthKey, status: Record<string, NodeStatus>): NodeStatus {
  if (!healthKey) return 'unmonitored';
  return status[healthKey] ?? 'unknown';
}
