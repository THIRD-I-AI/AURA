/**
 * PipelinePanel — the Palantir-style live pipeline command deck.
 *
 * A left-to-right service DAG rendered inside the terminal cockpit: every AURA
 * service is a node coloured by live /system/health status, edges show the data
 * flow, a log rail streams real SSE events, a scrubber replays recent history,
 * an inspector drills into any node, and the control row drives streaming
 * pipelines (start/stop/pause/resume) + UASR recovery approvals — all against
 * the real backend. No synthetic data: offline simply shows declared status
 * and a quiet rail.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { streamingService, healingService, type PendingRecovery } from '../../services/api';
import {
  computePipelineLayout,
  COL_W,
  PAD_X,
  STAGE_LABELS,
  STATUS_GLYPH,
  type PositionedNode,
  type NodeStatus,
} from '../pipeline/topology';
import { usePipelineTelemetry, nodeStatus } from '../pipeline/usePipelineTelemetry';
import '../pipeline/pipeline.css';

const LAYOUT = computePipelineLayout();
/* Extra viewBox band above the graph for the stage lane headers — keeps the
   topology module pure (its coordinates are untouched). */
const LABEL_BAND = 22;
const VIEW_H = LAYOUT.height + LABEL_BAND;
/* Don't upscale past this: giant node text reads as a mistake, not a feature. */
const MAX_SCALE = 1.6;

function edgePath(x1: number, y1: number, x2: number, y2: number): string {
  const dx = Math.max(40, (x2 - x1) * 0.5);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

/* Stages actually present, with their column x — drives the lane headers. */
const STAGE_COLS = [...new Set(LAYOUT.nodes.map((n) => n.stage))].sort((a, b) => a - b)
  .map((stage) => ({ stage, x: PAD_X + stage * COL_W, label: STAGE_LABELS[stage] ?? `STAGE ${stage}` }));

/* nodeId → ids of edges touching it, for hover/selection highlighting. */
const INCIDENT: Record<string, Set<string>> = {};
for (const e of LAYOUT.edges) {
  (INCIDENT[e.source] ??= new Set()).add(e.id);
  (INCIDENT[e.target] ??= new Set()).add(e.id);
}

export default function PipelinePanel(_props: IDockviewPanelProps) {
  const t = usePipelineTelemetry();
  const [selected, setSelected] = useState<string | null>('api_gateway');
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string>('');
  const [scrub, setScrub] = useState<number | null>(null); // null = live tail
  const [hovered, setHovered] = useState<string | null>(null);
  const railRef = useRef<HTMLDivElement | null>(null);

  /* Measure the graph box and scale the DAG to fill the wrap WIDTH (capped at
     MAX_SCALE). Scaling by both axes letterboxed the DAG to a sliver whenever
     the panel was short (e.g. docked in a stacked tab group), because the
     height term dominated the min(). Left-to-right flow is read across, so we
     fill the width and let the height overflow into a vertical scroll — the
     wrap sets overflow-y:auto for exactly this. */
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState<{ w: number; h: number } | null>(null);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return; // jsdom: fall back to native size
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r && r.width > 0 && r.height > 0) setBox({ w: r.width, h: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const scale = box ? Math.min(MAX_SCALE, box.w / LAYOUT.width) : 1;

  /* Highlight follows hover, falling back to the pinned selection. */
  const focusId = hovered ?? selected;
  const hotEdges = focusId ? INCIDENT[focusId] : undefined;

  const selectedNode = useMemo(
    () => LAYOUT.nodes.find((n) => n.id === selected) ?? null,
    [selected],
  );

  // Timeline: when scrubbing, only show events up to the scrub index.
  const shownEvents = useMemo(() => {
    if (scrub == null) return t.events;
    return t.events.slice(0, scrub + 1);
  }, [t.events, scrub]);

  const runControl = useCallback(async (
    action: 'start' | 'stop' | 'pause' | 'resume',
    pipelineId: string,
  ) => {
    setBusy(`${action}:${pipelineId}`);
    try {
      await streamingService[action](pipelineId);
    } catch {
      /* surfaced via the next telemetry reconcile */
    } finally {
      setBusy(null);
    }
  }, []);

  const nodeCls = (n: PositionedNode, status: NodeStatus) =>
    [
      'pl-node',
      `pl-kind-${n.kind}`,
      `pl-status-${status}`,
      selected === n.id ? 'is-selected' : '',
    ].filter(Boolean).join(' ');

  return (
    <div data-testid="pipeline-panel" className="aura-panel pl-panel">
      {/* ── header / overall status ─────────────────────────────────── */}
      <div className="pl-head">
        <span className={`pl-overall pl-overall-${t.overall}`}>
          {t.overall.toUpperCase()}
        </span>
        <span className="pl-head-metric">{t.healthyServices}/{t.totalServices} services</span>
        {t.huScore != null && (
          <span className="pl-head-metric">Hᵤ {t.huScore.toFixed(3)}</span>
        )}
        <span className={`pl-conn ${t.connected ? 'is-on' : 'is-off'}`}>
          {t.connected ? 'stream live' : 'stream offline'}
        </span>
        <span className="pl-head-spacer" />
        {t.lastUpdate && (
          <span className="pl-head-ts">upd {new Date(t.lastUpdate).toLocaleTimeString()}</span>
        )}
      </div>

      <div className="pl-body">
        {/* ── the flow DAG ──────────────────────────────────────────── */}
        <div className="pl-graph-wrap" ref={wrapRef}>
          <svg
            className={`pl-graph${t.connected ? ' is-live' : ''}${hotEdges ? ' has-focus' : ''}`}
            viewBox={`0 ${-LABEL_BAND} ${LAYOUT.width} ${VIEW_H}`}
            width={Math.max(1, Math.round(LAYOUT.width * scale))}
            height={Math.max(1, Math.round(VIEW_H * scale))}
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label="AURA service pipeline"
          >
            <defs>
              <marker id="pl-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                <path d="M0,0 L8,4 L0,8 Z" className="pl-arrow-head" />
              </marker>
            </defs>
            {/* stage lane headers */}
            <g className="pl-lanes">
              {STAGE_COLS.map((s) => (
                <text key={s.stage} className="pl-lane-label" x={s.x} y={-9}>{s.label}</text>
              ))}
            </g>
            {/* edges */}
            <g className="pl-edges">
              {LAYOUT.edges.map((e) => (
                <path
                  key={e.id}
                  d={edgePath(e.x1, e.y1, e.x2, e.y2)}
                  className={`pl-edge pl-edge-${e.kind ?? 'flow'}${hotEdges?.has(e.id) ? ' is-hot' : ''}`}
                  markerEnd={e.kind === 'flow' ? 'url(#pl-arrow)' : undefined}
                />
              ))}
            </g>
            {/* nodes */}
            <g className="pl-nodes">
              {LAYOUT.nodes.map((n) => {
                const status = nodeStatus(n.healthKey, t.serviceStatus);
                return (
                  <g
                    key={n.id}
                    transform={`translate(${n.x},${n.y})`}
                    className={nodeCls(n, status)}
                    onClick={() => setSelected(n.id)}
                    onMouseEnter={() => setHovered(n.id)}
                    onMouseLeave={() => setHovered((h) => (h === n.id ? null : h))}
                    role="button"
                    tabIndex={0}
                    aria-label={`${n.label} — ${status}`}
                    onKeyDown={(ev) => { if (ev.key === 'Enter') setSelected(n.id); }}
                  >
                    <rect className="pl-node-box" width={150} height={56} rx={2} />
                    <text className="pl-node-glyph" x={12} y={22}>{STATUS_GLYPH[status]}</text>
                    <text className="pl-node-label" x={28} y={22}>{n.label}</text>
                    <text className="pl-node-sub" x={12} y={42}>
                      {n.port ? `:${n.port}` : n.kind}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>

        {/* ── inspector ─────────────────────────────────────────────── */}
        <aside className="pl-inspector">
          {selectedNode ? (
            <>
              <div className="pl-insp-title">
                <span className={`pl-glyph pl-status-${nodeStatus(selectedNode.healthKey, t.serviceStatus)}`}>
                  {STATUS_GLYPH[nodeStatus(selectedNode.healthKey, t.serviceStatus)]}
                </span>
                {selectedNode.label}
              </div>
              <dl className="pl-insp-grid">
                <dt>kind</dt><dd>{selectedNode.kind}</dd>
                <dt>stage</dt><dd>{selectedNode.stage}</dd>
                {selectedNode.port && (<><dt>port</dt><dd>:{selectedNode.port}</dd></>)}
                <dt>status</dt><dd>{nodeStatus(selectedNode.healthKey, t.serviceStatus)}</dd>
                <dt>monitored</dt><dd>{selectedNode.healthKey ? 'yes' : 'no'}</dd>
              </dl>
              <p className="pl-insp-role">{selectedNode.role}</p>

              {/* UASR node → recovery approvals */}
              {selectedNode.id === 'uasr' && (
                <UasrRecoveries note={note} setNote={setNote} />
              )}

              {/* connectors/orchestration → stream pipeline controls */}
              {(selectedNode.id === 'connectors' || selectedNode.id === 'orchestration') && (
                <div className="pl-pipelines">
                  <div className="pl-insp-sub">stream pipelines ({t.pipelines.length})</div>
                  {t.pipelines.length === 0 && <div className="pl-empty">none running</div>}
                  {t.pipelines.map((p) => (
                    <div key={p.id} className="pl-pipe-row">
                      <span className="pl-pipe-name" title={p.description}>{p.name}</span>
                      <span className={`pl-pipe-status st-${p.status}`}>{p.status}</span>
                      <span className="pl-pipe-btns">
                        <button disabled={!!busy} onClick={() => runControl('start', p.id)} aria-label={`Start ${p.name}`} title="Start"><span aria-hidden="true">▶</span></button>
                        <button disabled={!!busy} onClick={() => runControl('pause', p.id)} aria-label={`Pause ${p.name}`} title="Pause"><span aria-hidden="true">❚❚</span></button>
                        <button disabled={!!busy} onClick={() => runControl('resume', p.id)} aria-label={`Resume ${p.name}`} title="Resume"><span aria-hidden="true">⟳</span></button>
                        <button disabled={!!busy} onClick={() => runControl('stop', p.id)} aria-label={`Stop ${p.name}`} title="Stop"><span aria-hidden="true">■</span></button>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="pl-empty">select a node</div>
          )}
        </aside>
      </div>

      {/* ── log rail + timeline scrubber ──────────────────────────────── */}
      <div className="pl-rail-wrap">
        <div className="pl-rail-head">
          <span>event stream</span>
          <span className="pl-rail-count">{shownEvents.length}/{t.events.length}</span>
          {scrub != null && (
            <button className="pl-live-btn" onClick={() => setScrub(null)}>↩ live</button>
          )}
        </div>
        <div className="pl-rail" ref={railRef}>
          {shownEvents.length === 0 && <div className="pl-empty">waiting for events…</div>}
          {shownEvents.slice(-120).map((e) => (
            <div key={e.seq} className={`pl-rail-row type-${e.type}`}>
              <span className="pl-rail-ts">{new Date(e.ts).toLocaleTimeString()}</span>
              <span className="pl-rail-line">{e.line}</span>
            </div>
          ))}
        </div>
        {t.events.length > 1 && (
          <input
            className="pl-scrub"
            type="range"
            min={0}
            max={t.events.length - 1}
            value={scrub == null ? t.events.length - 1 : scrub}
            onChange={(ev) => {
              const v = Number(ev.target.value);
              setScrub(v >= t.events.length - 1 ? null : v);
            }}
          />
        )}
      </div>
    </div>
  );
}

/** UASR recovery approvals — fetched on demand when the sidecar node is open. */
function UasrRecoveries({ note, setNote }: { note: string; setNote: (s: string) => void }) {
  const [pending, setPending] = useState<PendingRecovery[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const p = await healingService.pending();
      setPending(p);
    } catch {
      setPending([]);
    } finally {
      setLoaded(true);
    }
  }, []);

  // lazy first load
  if (!loaded) { void load(); }

  const decide = useCallback(async (id: string, kind: 'approve' | 'reject') => {
    setBusyId(id);
    try {
      if (kind === 'approve') await healingService.approve(id, 'operator', note || undefined);
      else await healingService.reject(id, 'operator', note || 'rejected from cockpit');
      await load();
    } catch {
      /* ignore — reconciled on reload */
    } finally {
      setBusyId(null);
    }
  }, [note, load]);

  return (
    <div className="pl-recov">
      <div className="pl-insp-sub">pending recoveries ({pending.length})</div>
      {pending.length === 0 && <div className="pl-empty">no recoveries awaiting review</div>}
      {pending.length > 0 && (
        <input
          className="pl-note"
          placeholder="decision note…"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      )}
      {pending.map((r) => (
        <div key={r.id} className="pl-recov-row">
          <div className="pl-recov-meta">
            <span className="pl-recov-src">{r.source_id ?? '—'}</span>
            <span className="pl-recov-diag" title={r.diagnosis ?? ''}>{r.diagnosis ?? r.status}</span>
          </div>
          <div className="pl-recov-btns">
            <button className="ok" disabled={busyId === r.id} onClick={() => decide(r.id, 'approve')}>approve</button>
            <button className="no" disabled={busyId === r.id} onClick={() => decide(r.id, 'reject')}>reject</button>
          </div>
        </div>
      ))}
    </div>
  );
}
