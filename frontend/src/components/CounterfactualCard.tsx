import React, { useState } from 'react';

export interface CounterfactualChallenge {
  text: string;
  severity: 'low' | 'medium' | 'high';
  suggested_check?: string | null;
}

export interface CounterfactualOperatorView {
  record_id: string;
  headline: string;
  point_estimate: number;
  ci: [number, number];
  confidence: 'low' | 'medium' | 'high';
  top_challenges: CounterfactualChallenge[];
  audit_record_hash: string;
}

const CONFIDENCE_BG: Record<string, string> = {
  low: 'rgba(220, 38, 38, 0.18)',
  medium: 'rgba(202, 138, 4, 0.18)',
  high: 'rgba(5, 150, 105, 0.18)',
};

const CONFIDENCE_FG: Record<string, string> = {
  low: '#fca5a5',
  medium: '#fde68a',
  high: '#86efac',
};

const CONFIDENCE_BORDER: Record<string, string> = {
  low: '#7f1d1d',
  medium: '#854d0e',
  high: '#065f46',
};

interface Props {
  artifact: CounterfactualOperatorView;
}

const CounterfactualCard: React.FC<Props> = ({ artifact }) => {
  const [showDebate, setShowDebate] = useState(false);

  return (
    <div
      data-testid="counterfactual-card"
      style={{
        border: '1px solid var(--border, #1e293b)',
        background: 'var(--card-bg, rgba(15, 23, 42, 0.6))',
        borderRadius: 8,
        padding: 16,
        margin: '12px 0',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <h3 style={{ margin: 0, fontSize: 16, color: 'var(--text-primary, #f1f5f9)', fontWeight: 500 }}>
          {artifact.headline}
        </h3>
        <span
          data-testid="confidence-badge"
          style={{
            padding: '2px 8px',
            borderRadius: 4,
            fontSize: 12,
            background: CONFIDENCE_BG[artifact.confidence],
            color: CONFIDENCE_FG[artifact.confidence],
            border: `1px solid ${CONFIDENCE_BORDER[artifact.confidence]}`,
            whiteSpace: 'nowrap',
          }}
        >
          {artifact.confidence}
        </span>
      </div>

      <div style={{ marginTop: 8, fontSize: 13, color: 'var(--text-secondary, #cbd5e1)' }}>
        Point estimate{' '}
        <span style={{ fontFamily: 'monospace' }}>{artifact.point_estimate.toFixed(2)}</span>
        {' '}· 95% CI{' '}
        <span style={{ fontFamily: 'monospace' }}>
          [{artifact.ci[0].toFixed(2)}, {artifact.ci[1].toFixed(2)}]
        </span>
      </div>

      <button
        type="button"
        onClick={() => setShowDebate(s => !s)}
        style={{
          marginTop: 12,
          background: 'transparent',
          border: 'none',
          color: 'var(--accent, #38bdf8)',
          fontSize: 12,
          cursor: 'pointer',
          padding: 0,
          textDecoration: 'underline',
          textUnderlineOffset: 2,
        }}
      >
        {showDebate ? 'Hide the debate' : 'See the debate'}
      </button>

      {showDebate && (
        <ul style={{ marginTop: 8, paddingLeft: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {artifact.top_challenges.length === 0 && (
            <li style={{ fontSize: 13, color: 'var(--text-secondary, #cbd5e1)', fontStyle: 'italic' }}>
              No challenges raised — refutation tests passed and the critic had no objections.
            </li>
          )}
          {artifact.top_challenges.map((c, i) => (
            <li key={i} style={{ fontSize: 13, color: 'var(--text-secondary, #cbd5e1)' }}>
              <span
                style={{
                  display: 'inline-block',
                  marginRight: 8,
                  padding: '1px 6px',
                  borderRadius: 3,
                  fontSize: 10,
                  background: CONFIDENCE_BG[c.severity],
                  color: CONFIDENCE_FG[c.severity],
                  border: `1px solid ${CONFIDENCE_BORDER[c.severity]}`,
                }}
              >
                {c.severity}
              </span>
              {c.text}
              {c.suggested_check && (
                <div style={{ marginLeft: 36, marginTop: 2, fontSize: 12, opacity: 0.7 }}>
                  → {c.suggested_check}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <div
        style={{
          marginTop: 12,
          fontSize: 10,
          color: 'var(--text-muted, #64748b)',
          fontFamily: 'monospace',
        }}
      >
        audit_record_hash: {artifact.audit_record_hash.slice(0, 16)}…
      </div>
    </div>
  );
};

export default CounterfactualCard;
