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
  idle: 'Ready',
  planning: 'Planning…',
  executing: 'Executing…',
  streaming: 'Streaming…',
  done: 'Complete',
  error: 'Error',
};

const PHASE_ICONS: Record<AgentPhase, string> = {
  idle: '⚡',
  planning: '🧠',
  executing: '⚙️',
  streaming: '📡',
  done: '✅',
  error: '❌',
};

/* ────────────────────────────────────────────────────────────────── */

const AgentPanel: React.FC<AgentPanelProps> = () => {
  const [prompt, setPrompt] = useState('');
  const [{ phase, plan, report, progress, error }, actions] = useAgentExecutor();
  const progressEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll progress feed
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
      {/* ── Prompt bar ──────────────────────────────────────────── */}
      <div className="agent-prompt">
        <textarea
          className="agent-prompt__input"
          placeholder="Describe your data engineering task in plain English…"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={busy}
          rows={2}
        />
        <button
          className="agent-prompt__submit"
          onClick={handleSubmit}
          disabled={busy || !prompt.trim()}
        >
          {busy && <span className="agent-spinner" />}
          {busy ? 'Working…' : '▶ Run Agent'}
        </button>
      </div>

      {/* ── Phase badge ─────────────────────────────────────────── */}
      {phase !== 'idle' && (
        <span className={`agent-phase agent-phase--${phase}`}>
          {PHASE_ICONS[phase]} {PHASE_LABELS[phase]}
        </span>
      )}

      {/* ── Error ───────────────────────────────────────────────── */}
      {error && (
        <div className="agent-error">
          ⚠️ {error}
        </div>
      )}

      {/* ── Live progress ───────────────────────────────────────── */}
      {progress.length > 0 && (
        <ProgressFeed items={progress} ref={progressEndRef} />
      )}

      {/* ── Report ──────────────────────────────────────────────── */}
      {report && <ReportCard report={report} />}

      {/* ── Plan preview (plan-only mode) ───────────────────────── */}
      {plan && !report && <PlanCard plan={plan} />}
    </div>
  );
};

/* ══════════════════════════════════════════════════════════════════ */
/* Sub-components                                                    */
/* ══════════════════════════════════════════════════════════════════ */

const ProgressFeed = React.forwardRef<HTMLDivElement, { items: AgentProgress[] }>(
  ({ items }, ref) => (
    <div className="agent-progress">
      <div className="agent-progress__header">Live progress</div>
      {items.map((p, i) => (
        <div className="agent-progress__item" key={i}>
          <span className="agent-progress__agent">[{p.agent}]</span>
          <span>{p.message}</span>
        </div>
      ))}
      <div ref={ref} />
    </div>
  ),
);
ProgressFeed.displayName = 'ProgressFeed';

/* ─────────────────────────────────────────────────────────────── */

const PlanCard: React.FC<{ plan: AgentPlan }> = ({ plan }) => (
  <div className="agent-plan">
    <div className="agent-plan__header">
      📋 Execution Plan — {plan.tasks.length} task{plan.tasks.length !== 1 ? 's' : ''}
    </div>
    <ul className="agent-plan__tasks">
      {plan.tasks.map((t) => (
        <li className="agent-plan__task" key={t.id}>
          <span className="agent-plan__task-agent">{t.agent_name}</span>
          <span className="agent-plan__task-desc">{t.description}</span>
          {t.depends_on.length > 0 && (
            <span className="agent-plan__task-deps">
              ← {t.depends_on.join(', ')}
            </span>
          )}
        </li>
      ))}
    </ul>
  </div>
);

/* ─────────────────────────────────────────────────────────────── */

const ReportCard: React.FC<{ report: AgentReport }> = ({ report }) => {
  const allSuggestions = Object.values(report.tasks).flatMap((t) => t.suggestions);

  return (
    <div className="agent-report">
      <div className="agent-report__header">
        <span>Execution Report</span>
        <span
          className={
            report.success
              ? 'agent-report__badge--success'
              : 'agent-report__badge--fail'
          }
        >
          {report.success ? '✓ Success' : '✗ Failed'}
        </span>
      </div>

      <div className="agent-report__body">
        <p className="agent-report__summary">
          {report.summary} — {report.duration_ms.toFixed(0)} ms
        </p>

        <div className="agent-report__tasks">
          {Object.entries(report.tasks).map(([tid, task]) => (
            <div className="agent-report__task" key={tid}>
              <div className="agent-report__task-head">
                <span className="agent-report__task-title">{tid}</span>
                <span
                  className={`agent-report__task-status agent-report__task-status--${task.status}`}
                >
                  {task.status}
                </span>
              </div>
              {task.error && (
                <div className="agent-report__task-error">
                  {task.error}
                </div>
              )}
              {task.output && (
                <pre className="agent-report__task-output">
                  {JSON.stringify(task.output, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>

        {allSuggestions.length > 0 && (
          <div className="agent-report__suggestions">
            <h4>💡 Suggestions</h4>
            <ul>
              {allSuggestions.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
};

export default AgentPanel;
