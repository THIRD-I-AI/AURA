import React, { useState, useRef, useEffect } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import {
  useAgentExecutor,
  type AgentPhase,
  type AgentReport,
  type AgentPlan,
  type AgentProgress,
} from '../hooks/useAgentExecutor';
import './AgentPanel.css';

interface AgentPanelProps {
  setCurrentPage?: (page: PageType) => void;
}

const PHASE_LABELS: Record<AgentPhase, string> = {
  idle:      'Ready',
  planning:  'Planning',
  executing: 'Executing',
  streaming: 'Streaming',
  done:      'Complete',
  error:     'Error',
};

// ── SVG phase icons ───────────────────────────────────────────────────────────

const BoltIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
  </svg>
);

const BrainIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96-.46 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/>
    <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96-.46 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/>
  </svg>
);

const GearIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3"/>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/>
  </svg>
);

const WaveIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
  </svg>
);

const CheckCircleIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#34d399" strokeWidth="2.5" strokeLinecap="round">
    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
    <polyline points="22 4 12 14.01 9 11.01"/>
  </svg>
);

const XCircleIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="2.5" strokeLinecap="round">
    <circle cx="12" cy="12" r="10"/>
    <line x1="15" y1="9" x2="9" y2="15"/>
    <line x1="9" y1="9" x2="15" y2="15"/>
  </svg>
);

const AlertTriangleIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
    <line x1="12" y1="9" x2="12" y2="13"/>
    <line x1="12" y1="17" x2="12.01" y2="17"/>
  </svg>
);

const PlayIcon = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
    <polygon points="5 3 19 12 5 21 5 3"/>
  </svg>
);

const ListIcon = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="8"  y1="6"  x2="21" y2="6"/>
    <line x1="8"  y1="12" x2="21" y2="12"/>
    <line x1="8"  y1="18" x2="21" y2="18"/>
    <line x1="3"  y1="6"  x2="3.01" y2="6"/>
    <line x1="3"  y1="12" x2="3.01" y2="12"/>
    <line x1="3"  y1="18" x2="3.01" y2="18"/>
  </svg>
);

const LightbulbIcon = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="9"  y1="18" x2="15" y2="18"/>
    <line x1="10" y1="22" x2="14" y2="22"/>
    <path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"/>
  </svg>
);

const CheckSmallIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#34d399" strokeWidth="2.5" strokeLinecap="round">
    <polyline points="20 6 9 17 4 12"/>
  </svg>
);

const XSmallIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="2.5" strokeLinecap="round">
    <line x1="18" y1="6" x2="6" y2="18"/>
    <line x1="6"  y1="6" x2="18" y2="18"/>
  </svg>
);

const DotIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
    <circle cx="12" cy="12" r="3"/>
  </svg>
);

// ─────────────────────────────────────────────────────────────────────────────

const PHASE_ICONS: Record<AgentPhase, React.ReactNode> = {
  idle:      <BoltIcon />,
  planning:  <BrainIcon />,
  executing: <GearIcon />,
  streaming: <WaveIcon />,
  done:      <CheckCircleIcon />,
  error:     <XCircleIcon />,
};

const SUGGESTION_CHIPS = [
  'Analyze my dataset',
  'Clean duplicate rows',
  'Export top 100 records',
  'Summarize numeric columns',
  'Filter by date range',
  'Convert to Parquet',
];

// ─────────────────────────────────────────────────────────────────────────────

const AgentPanel: React.FC<AgentPanelProps> = () => {
  const [prompt, setPrompt] = useState('');
  const [{ phase, plan, report, progress, error }, actions] = useAgentExecutor();
  const progressEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    progressEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [progress.length]);

  const handleSubmit = () => {
    const text = prompt.trim();
    if (!text) return;
    actions.stream(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const busy = phase === 'planning' || phase === 'executing' || phase === 'streaming';

  return (
    <div className="agent-panel">

      {/* ── Suggestion chips ────────────────────────────────────── */}
      <div className="agent-suggestions">
        {SUGGESTION_CHIPS.map((chip) => (
          <button
            key={chip}
            className="agent-suggestion-chip"
            onClick={() => setPrompt(chip)}
            disabled={busy}
          >
            {chip}
          </button>
        ))}
      </div>

      {/* ── Prompt bar ──────────────────────────────────────────── */}
      <div className="agent-prompt-bar">
        <textarea
          className="agent-prompt-input"
          placeholder="Describe your data engineering task in plain English…"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={busy}
          rows={2}
        />
        <button
          onClick={handleSubmit}
          disabled={busy || !prompt.trim()}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-2)',
            padding: 'var(--space-2-5) var(--space-4)',
            background: busy || !prompt.trim() ? 'var(--bg-elevated)' : '#22c55e',
            border: '1px solid',
            borderColor: busy || !prompt.trim() ? 'var(--border-default)' : '#16a34a',
            borderRadius: 'var(--radius-md)',
            color: busy || !prompt.trim() ? 'var(--text-disabled)' : '#fff',
            fontWeight: 600,
            fontSize: 'var(--font-sm)',
            cursor: busy || !prompt.trim() ? 'not-allowed' : 'pointer',
            fontFamily: 'var(--font-sans)',
            transition: 'all var(--dur-fast)',
            whiteSpace: 'nowrap',
            alignSelf: 'flex-end',
          }}
        >
          {busy ? (
            <>
              <span
                style={{
                  width: 12,
                  height: 12,
                  border: '2px solid currentColor',
                  borderTopColor: 'transparent',
                  borderRadius: '50%',
                  animation: 'spin 0.7s linear infinite',
                  display: 'inline-block',
                }}
              />
              Working…
            </>
          ) : (
            <>
              <PlayIcon /> Run Agent
            </>
          )}
        </button>
      </div>

      {/* ── Phase badge ─────────────────────────────────────────── */}
      {phase !== 'idle' && (
        <div>
          <span className={`phase-badge phase-badge--${phase}`}>
            {PHASE_ICONS[phase]}
            {PHASE_LABELS[phase]}
          </span>
        </div>
      )}

      {/* ── Error ───────────────────────────────────────────────── */}
      {error && (
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 'var(--space-2)',
            padding: 'var(--space-3) var(--space-4)',
            background: 'var(--red-dim)',
            border: '1px solid var(--red-border)',
            borderRadius: 'var(--radius-lg)',
            fontSize: 'var(--font-sm)',
            color: '#f87171',
          }}
        >
          <span style={{ flexShrink: 0, marginTop: 1 }}><AlertTriangleIcon /></span>
          <span>{error}</span>
        </div>
      )}

      {/* ── Live progress feed ───────────────────────────────────── */}
      {progress.length > 0 && (
        <ProgressFeed items={progress} ref={progressEndRef} />
      )}

      {/* ── Report card ──────────────────────────────────────────── */}
      {report && <ReportCard report={report} />}

      {/* ── Plan preview ─────────────────────────────────────────── */}
      {plan && !report && <PlanCard plan={plan} />}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
};

// ── ProgressFeed ─────────────────────────────────────────────────────────────

const ProgressFeed = React.forwardRef<HTMLDivElement, { items: AgentProgress[] }>(
  ({ items }, ref) => (
    <div className="agent-feed">
      {items.map((p, i) => {
        const lineType = p.message.toLowerCase().includes('error')
          ? 'error'
          : p.message.toLowerCase().includes('done') || p.message.toLowerCase().includes('success') || p.message.toLowerCase().includes('complete')
          ? 'success'
          : 'info';
        const ts = new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        return (
          <div key={i} className={`agent-feed-line agent-feed-line--${lineType}`}>
            <span className="agent-feed-line__ts">{ts}</span>
            <span style={{ color: 'var(--text-tertiary)', flexShrink: 0 }}>[{p.agent}]</span>
            <span className="agent-feed-line__msg">{p.message}</span>
          </div>
        );
      })}
      <div ref={ref} />
    </div>
  ),
);
ProgressFeed.displayName = 'ProgressFeed';

// ── PlanCard ─────────────────────────────────────────────────────────────────

const PlanCard: React.FC<{ plan: AgentPlan }> = ({ plan }) => (
  <div className="agent-plan-card">
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-2)',
        padding: 'var(--space-3) var(--space-4)',
        borderBottom: '1px solid var(--border-subtle)',
        fontSize: 'var(--font-sm)',
        fontWeight: 600,
        color: 'var(--text-primary)',
      }}
    >
      <ListIcon />
      Execution Plan
      <span
        style={{
          marginLeft: 'auto',
          fontSize: '10px',
          fontWeight: 600,
          padding: '1px 6px',
          borderRadius: 'var(--radius-full)',
          background: 'var(--blue-dim)',
          color: '#60a5fa',
          border: '1px solid var(--accent-border)',
        }}
      >
        {plan.tasks.length} task{plan.tasks.length !== 1 ? 's' : ''}
      </span>
    </div>
    <ul className="agent-task-list">
      {plan.tasks.map((t) => (
        <li key={t.id} className="agent-task-item">
          <span className="agent-task-icon"><DotIcon /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: 'var(--font-xs)' }}>
              {t.agent_name}
            </div>
            <div style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-xs)', marginTop: 1 }}>
              {t.description}
            </div>
          </div>
          {t.depends_on.length > 0 && (
            <span
              style={{
                fontSize: 10,
                color: 'var(--text-disabled)',
                flexShrink: 0,
              }}
            >
              dep: {t.depends_on.join(', ')}
            </span>
          )}
        </li>
      ))}
    </ul>
  </div>
);

// ── ReportCard ───────────────────────────────────────────────────────────────

const ReportCard: React.FC<{ report: AgentReport }> = ({ report }) => {
  const allSuggestions = Object.values(report.tasks).flatMap((t) => t.suggestions);

  return (
    <div className="agent-report-card">
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: 'var(--space-3) var(--space-4)',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        <span style={{ fontSize: 'var(--font-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
          Execution Report
        </span>
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            fontSize: '10px',
            fontWeight: 700,
            padding: '2px 8px',
            borderRadius: 'var(--radius-full)',
            border: '1px solid',
            background: report.success ? 'var(--green-dim)' : 'var(--red-dim)',
            color: report.success ? '#34d399' : '#f87171',
            borderColor: report.success ? 'var(--green-border)' : 'var(--red-border)',
          }}
        >
          {report.success ? <CheckSmallIcon /> : <XSmallIcon />}
          {report.success ? 'Success' : 'Failed'}
        </span>
      </div>

      {/* Summary */}
      <div
        style={{
          padding: 'var(--space-3) var(--space-4)',
          borderBottom: '1px solid var(--border-subtle)',
          fontSize: 'var(--font-xs)',
          color: 'var(--text-tertiary)',
        }}
      >
        {report.summary}
        {report.duration_ms > 0 && (
          <span style={{ marginLeft: 'var(--space-2)', fontFamily: 'var(--font-mono)', color: 'var(--text-disabled)' }}>
            {report.duration_ms.toFixed(0)}ms
          </span>
        )}
      </div>

      {/* Tasks breakdown */}
      <ul className="agent-task-list">
        {Object.entries(report.tasks).map(([tid, task]) => (
          <li
            key={tid}
            className={`agent-task-item${task.status === 'done' || task.status === 'success' ? ' agent-task-item--done' : task.status === 'error' ? ' agent-task-item--error' : ''}`}
          >
            <span className="agent-task-icon">
              {task.status === 'done' || task.status === 'success' ? <CheckSmallIcon /> : task.status === 'error' ? <XSmallIcon /> : <DotIcon />}
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 'var(--font-xs)' }}>{tid}</div>
              {task.error && (
                <div style={{ fontSize: 10, color: '#f87171', marginTop: 2 }}>{task.error}</div>
              )}
              {task.output && (
                <pre
                  style={{
                    margin: '4px 0 0',
                    fontSize: 10,
                    fontFamily: 'var(--font-mono)',
                    color: 'var(--text-tertiary)',
                    background: 'var(--bg-sunken)',
                    padding: '4px 6px',
                    borderRadius: 'var(--radius-md)',
                    overflow: 'auto',
                    maxHeight: 60,
                  }}
                >
                  {JSON.stringify(task.output, null, 2)}
                </pre>
              )}
            </div>
            <span
              style={{
                fontSize: 10,
                fontFamily: 'var(--font-mono)',
                color: 'var(--text-disabled)',
                flexShrink: 0,
              }}
            >
              {task.duration_ms > 0 ? `${task.duration_ms.toFixed(0)}ms` : ''}
            </span>
          </li>
        ))}
      </ul>

      {/* Suggestions */}
      {allSuggestions.length > 0 && (
        <div
          style={{
            padding: 'var(--space-3) var(--space-4)',
            borderTop: '1px solid var(--border-subtle)',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-2)',
              fontSize: 'var(--font-xs)',
              fontWeight: 600,
              color: 'var(--text-tertiary)',
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              marginBottom: 'var(--space-2)',
            }}
          >
            <LightbulbIcon /> Suggestions
          </div>
          <div className="agent-suggestions">
            {allSuggestions.map((s, i) => (
              <span key={i} className="agent-suggestion-chip">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentPanel;
