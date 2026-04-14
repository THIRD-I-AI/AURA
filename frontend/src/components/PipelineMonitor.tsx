/**
 * PipelineMonitor — live DAG/stage view for a running pipeline.
 *
 * Given a `runId`, subscribes to SSE topic `pipeline:{runId}` and renders
 *   - the step plan (from the initial `data` event)
 *   - per-stage live status (source → transforms → sink → complete)
 *   - final run summary on `complete`
 */
import React, { useState } from 'react';
import { useSSE, type SSEEvent } from '../hooks/useSSE';

export interface PipelineStepMeta {
  id: string;
  name: string;
  type: string;
}

export interface PipelineRunSummary {
  status?: string;
  rows_read?: number;
  rows_written?: number;
  duration_ms?: number;
  error?: string | null;
  output?: Record<string, unknown> | null;
}

type StageStatus = 'pending' | 'running' | 'done' | 'error';

interface StageState {
  key: string;
  label: string;
  status: StageStatus;
  detail?: string;
}

const BASE_STAGES: StageState[] = [
  { key: 'source',    label: 'Load source',    status: 'pending' },
  { key: 'build_sql', label: 'Build SQL',      status: 'pending' },
  { key: 'transform', label: 'Transforms',     status: 'pending' },
  { key: 'sink',      label: 'Write sink',     status: 'pending' },
];

const StatusDot: React.FC<{ status: StageStatus }> = ({ status }) => {
  const color =
    status === 'done'    ? 'var(--fg-green)'  :
    status === 'running' ? 'var(--fg-blue)'   :
    status === 'error'   ? 'var(--fg-red)'    :
                           'var(--text-disabled)';
  return (
    <span
      style={{
        display: 'inline-block',
        width: 10,
        height: 10,
        borderRadius: '50%',
        background: color,
        flexShrink: 0,
        animation: status === 'running' ? 'live-pulse 1.5s ease-in-out infinite' : undefined,
      }}
    />
  );
};

export const PipelineMonitor: React.FC<{
  runId: string;
  onComplete?: (summary: PipelineRunSummary) => void;
}> = ({ runId, onComplete }) => {
  const [stages, setStages] = useState<StageState[]>(BASE_STAGES);
  const [steps, setSteps] = useState<PipelineStepMeta[]>([]);
  const [stepStatus, setStepStatus] = useState<Record<string, StageStatus>>({});
  const [summary, setSummary] = useState<PipelineRunSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [percent, setPercent] = useState(0);

  const markStage = (key: string, status: StageStatus, detail?: string) => {
    setStages((prev) => prev.map((s) => (s.key === key ? { ...s, status, detail } : s)));
  };

  useSSE({
    topic: `pipeline:${runId}`,
    enabled: !!runId,
    onEvent: (ev: SSEEvent) => {
      if (ev.type === 'data') {
        const p = ev.payload as { kind?: string; steps?: PipelineStepMeta[] };
        if (p.kind === 'plan' && Array.isArray(p.steps)) {
          setSteps(p.steps);
        }
        return;
      }
      if (ev.type === 'progress') {
        const p = ev.payload as {
          stage?: string; message?: string; percent?: number;
          step_id?: string; step_index?: number;
        };
        if (typeof p.percent === 'number') setPercent(p.percent);
        if (p.stage === 'transform' && p.step_id) {
          setStepStatus((prev) => {
            const next = { ...prev, [p.step_id as string]: 'running' as StageStatus };
            // mark earlier steps as done
            steps.forEach((s, i) => {
              if (p.step_index !== undefined && i < p.step_index) next[s.id] = 'done';
            });
            return next;
          });
          markStage('transform', 'running', p.message);
          // preceding stages must be done
          markStage('source', 'done');
          markStage('build_sql', 'done');
        } else if (p.stage) {
          markStage(p.stage, 'running', p.message);
          // upstream stages are implicitly done
          const idx = BASE_STAGES.findIndex((s) => s.key === p.stage);
          if (idx >= 0) {
            BASE_STAGES.slice(0, idx).forEach((s) => markStage(s.key, 'done'));
          }
        }
        return;
      }
      if (ev.type === 'complete') {
        const p = ev.payload as { result?: PipelineRunSummary };
        setStages((prev) => prev.map((s) => ({ ...s, status: 'done' as StageStatus })));
        setStepStatus((prev) => {
          const next = { ...prev };
          steps.forEach((s) => { next[s.id] = 'done'; });
          return next;
        });
        setPercent(100);
        if (p.result) {
          setSummary(p.result);
          onComplete?.(p.result);
        }
        return;
      }
      if (ev.type === 'error') {
        const p = ev.payload as { error?: string };
        setError(p.error || 'Pipeline failed');
        setStages((prev) => prev.map((s) =>
          s.status === 'running' ? { ...s, status: 'error' as StageStatus } : s));
      }
    },
  });

  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-lg)',
        padding: 'var(--space-4)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-3)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 'var(--font-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
          Live Pipeline Run
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
          {runId.slice(0, 8)} · {percent}%
        </span>
      </div>

      {/* progress bar */}
      <div style={{ height: 4, background: 'var(--bg-elevated)', borderRadius: 'var(--radius-full)', overflow: 'hidden' }}>
        <div
          style={{
            height: '100%',
            width: `${percent}%`,
            background: error ? 'var(--fg-red)' : 'var(--accent)',
            transition: 'width var(--t-hover, 150ms) ease',
          }}
        />
      </div>

      {/* stage list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
        {stages.map((s) => (
          <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <StatusDot status={s.status} />
            <span style={{
              fontSize: 'var(--font-sm)',
              color: s.status === 'pending' ? 'var(--text-tertiary)' : 'var(--text-primary)',
              fontWeight: s.status === 'running' ? 600 : 500,
            }}>
              {s.label}
            </span>
            {s.detail && (
              <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
                {s.detail}
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Per-step sub-list */}
      {steps.length > 0 && (
        <div style={{
          borderTop: '1px solid var(--border-subtle)',
          paddingTop: 'var(--space-3)',
          display: 'flex', flexDirection: 'column', gap: 'var(--space-2)',
        }}>
          <div style={{
            fontSize: 10, fontWeight: 600, letterSpacing: '0.07em',
            textTransform: 'uppercase', color: 'var(--text-tertiary)',
          }}>
            Steps ({steps.length})
          </div>
          {steps.map((step) => {
            const st = stepStatus[step.id] || 'pending';
            return (
              <div key={step.id} style={{
                display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
                paddingLeft: 'var(--space-3)',
              }}>
                <StatusDot status={st} />
                <span style={{
                  fontSize: 'var(--font-xs)', fontFamily: 'var(--font-mono)',
                  color: st === 'pending' ? 'var(--text-disabled)' : 'var(--text-secondary)',
                }}>
                  {step.name}
                </span>
                <span style={{ fontSize: 10, color: 'var(--text-disabled)' }}>
                  {step.type}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* Completion summary */}
      {summary && (
        <div style={{
          borderTop: '1px solid var(--border-subtle)',
          paddingTop: 'var(--space-3)',
          display: 'flex', flexWrap: 'wrap', gap: 'var(--space-4)',
          fontSize: 'var(--font-xs)', fontFamily: 'var(--font-mono)',
          color: 'var(--text-tertiary)',
        }}>
          {summary.rows_read !== undefined && <span>in: {summary.rows_read.toLocaleString()}</span>}
          {summary.rows_written !== undefined && <span>out: {summary.rows_written.toLocaleString()}</span>}
          {summary.duration_ms !== undefined && <span>{summary.duration_ms.toFixed(0)}ms</span>}
        </div>
      )}

      {error && (
        <div style={{
          padding: 'var(--space-2) var(--space-3)',
          background: 'var(--red-dim)', border: '1px solid var(--red-border)',
          borderRadius: 'var(--radius-md)', color: 'var(--fg-red)',
          fontSize: 'var(--font-xs)',
        }}>
          {error}
        </div>
      )}
    </div>
  );
};

export default PipelineMonitor;
