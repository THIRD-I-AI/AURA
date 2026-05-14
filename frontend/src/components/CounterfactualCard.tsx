import React, { useState } from 'react';

export interface CounterfactualChallenge {
  text: string;
  severity: 'low' | 'medium' | 'high';
  suggested_check?: string | null;
}

export interface PropensitySummary {
  method: string;
  fragility: 'ok' | 'amber' | 'red';
  n_extreme: number;
  n_total: number;
  p05: number;
  p25: number;
  p50: number;
  p75: number;
  p95: number;
  mean: number;
}

export interface SensitivityPerturbation {
  refuter: string;
  estimate_after: number;
  passed: boolean;
}

export interface SensitivityBand {
  baseline: number;
  perturbations: SensitivityPerturbation[];
}

export interface CounterfactualOperatorView {
  record_id: string;
  headline: string;
  point_estimate: number;
  ci: [number, number];
  confidence: 'low' | 'medium' | 'high';
  top_challenges: CounterfactualChallenge[];
  audit_record_hash: string;
  // Sprint 14 additions — both optional so older artifacts that
  // pre-date the propensity work still render cleanly.
  propensity_summary?: PropensitySummary;
  sensitivity_band?: SensitivityBand;
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

// Reuses the confidence palette: red = fragile, amber = caution, ok = healthy.
// Mapping is deliberate — the operator already reads red badges as
// "trustworthiness problem" so the propensity badge speaks the same vocabulary.
const FRAGILITY_BG: Record<string, string> = {
  red:    CONFIDENCE_BG.low,
  amber:  CONFIDENCE_BG.medium,
  ok:     CONFIDENCE_BG.high,
};
const FRAGILITY_FG: Record<string, string> = {
  red:    CONFIDENCE_FG.low,
  amber:  CONFIDENCE_FG.medium,
  ok:     CONFIDENCE_FG.high,
};
const FRAGILITY_BORDER: Record<string, string> = {
  red:    CONFIDENCE_BORDER.low,
  amber:  CONFIDENCE_BORDER.medium,
  ok:     CONFIDENCE_BORDER.high,
};
const FRAGILITY_LABEL: Record<string, string> = {
  red:    'IPW-fragile',
  amber:  'check propensity',
  ok:     'propensity ok',
};

// ── Sprint 14 — propensity quantile bar ──────────────────────────────
//
// Renders a thin horizontal bar from 0 to 1 with a shaded band covering
// [p05, p95] (the central 90% of the propensity distribution) and a
// tick at the mean. When the fragility is non-ok, the bar carries a
// badge with the n_extreme fraction. The visual maps the math: a
// fragile estimate is one whose band crosses or hugs the 0/1 boundary,
// and the eye can see that without reading the numbers.

const PropensityBlock: React.FC<{ summary: PropensitySummary }> = ({ summary }) => {
  const extremeFrac = summary.n_total > 0 ? summary.n_extreme / summary.n_total : 0;
  // Clamp to [0, 1] for the visual; the math allows quantiles outside
  // [0, 1] in pathological fits but we don't want CSS overflows.
  const left = Math.max(0, Math.min(1, summary.p05)) * 100;
  const right = Math.max(0, Math.min(1, summary.p95)) * 100;
  const mean = Math.max(0, Math.min(1, summary.mean)) * 100;

  return (
    <div data-testid="propensity-block" style={{ marginTop: 10 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 12,
          color: 'var(--text-secondary, #cbd5e1)',
        }}
      >
        <span style={{ minWidth: 92 }}>Propensity ({summary.method})</span>
        <span
          data-testid="propensity-fragility"
          style={{
            padding: '1px 6px',
            borderRadius: 3,
            fontSize: 10,
            background: FRAGILITY_BG[summary.fragility],
            color: FRAGILITY_FG[summary.fragility],
            border: `1px solid ${FRAGILITY_BORDER[summary.fragility]}`,
          }}
        >
          {FRAGILITY_LABEL[summary.fragility]}
        </span>
        <span style={{ fontFamily: 'monospace', opacity: 0.8 }}>
          {summary.n_extreme}/{summary.n_total} extreme ({(extremeFrac * 100).toFixed(1)}%)
        </span>
      </div>
      <div
        style={{
          marginTop: 4,
          position: 'relative',
          height: 8,
          background: 'rgba(148, 163, 184, 0.15)',
          borderRadius: 4,
          overflow: 'hidden',
        }}
      >
        {/* Central 90% (p05 → p95) band */}
        <div
          data-testid="propensity-band"
          style={{
            position: 'absolute',
            left: `${left}%`,
            width: `${Math.max(0, right - left)}%`,
            top: 0,
            bottom: 0,
            background: FRAGILITY_BG[summary.fragility],
            borderLeft: `2px solid ${FRAGILITY_BORDER[summary.fragility]}`,
            borderRight: `2px solid ${FRAGILITY_BORDER[summary.fragility]}`,
          }}
        />
        {/* Mean tick */}
        <div
          data-testid="propensity-mean"
          style={{
            position: 'absolute',
            left: `calc(${mean}% - 1px)`,
            top: 0,
            bottom: 0,
            width: 2,
            background: FRAGILITY_FG[summary.fragility],
          }}
        />
      </div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 10,
          color: 'var(--text-muted, #64748b)',
          fontFamily: 'monospace',
          marginTop: 2,
        }}
      >
        <span>0.0</span>
        <span>p05={summary.p05.toFixed(2)}</span>
        <span>p50={summary.p50.toFixed(2)}</span>
        <span>p95={summary.p95.toFixed(2)}</span>
        <span>1.0</span>
      </div>
    </div>
  );
};

// ── Sprint 14 — sensitivity band ─────────────────────────────────────
//
// Renders the per-refuter perturbation as a small horizontal scale.
// Baseline anchors the centre; each refuter's estimate_after sits as a
// dot offset by (estimate_after - baseline). Passed refuters show in
// green, failed in amber. The visual answers "does any refuter break
// the headline?" at a glance: dots that pile up near zero = robust,
// dots that swing far = sensitive.

const SensitivityBlock: React.FC<{
  band: SensitivityBand;
  ci: [number, number];
}> = ({ band, ci }) => {
  // Domain for the visual: at minimum [ci[0], ci[1]], extended to
  // include the most extreme perturbation so all dots fit.
  const allValues = [
    band.baseline,
    ci[0],
    ci[1],
    ...band.perturbations.map(p => p.estimate_after),
  ];
  const minV = Math.min(...allValues);
  const maxV = Math.max(...allValues);
  const span = Math.max(maxV - minV, 1e-9);
  const baselinePct = ((band.baseline - minV) / span) * 100;

  return (
    <div data-testid="sensitivity-block" style={{ marginTop: 10 }}>
      <div
        style={{
          fontSize: 12,
          color: 'var(--text-secondary, #cbd5e1)',
          display: 'flex',
          gap: 8,
        }}
      >
        <span style={{ minWidth: 92 }}>Sensitivity</span>
        <span style={{ fontFamily: 'monospace', opacity: 0.8 }}>
          baseline {band.baseline.toFixed(2)} · {band.perturbations.length} refuters
        </span>
      </div>
      <div
        style={{
          marginTop: 4,
          position: 'relative',
          height: 18,
          background: 'rgba(148, 163, 184, 0.15)',
          borderRadius: 4,
        }}
      >
        {/* Baseline marker */}
        <div
          data-testid="sensitivity-baseline"
          style={{
            position: 'absolute',
            left: `calc(${baselinePct}% - 1px)`,
            top: 0,
            bottom: 0,
            width: 2,
            background: 'var(--text-primary, #f1f5f9)',
            opacity: 0.5,
          }}
        />
        {band.perturbations.map(p => {
          const pct = ((p.estimate_after - minV) / span) * 100;
          const color = p.passed
            ? CONFIDENCE_FG.high
            : CONFIDENCE_FG.medium;
          const border = p.passed
            ? CONFIDENCE_BORDER.high
            : CONFIDENCE_BORDER.medium;
          return (
            <div
              key={p.refuter}
              data-testid={`sensitivity-dot-${p.refuter}`}
              title={`${p.refuter}: ${p.estimate_after.toFixed(3)} ${p.passed ? '(passed)' : '(failed)'}`}
              style={{
                position: 'absolute',
                left: `calc(${pct}% - 5px)`,
                top: 4,
                width: 10,
                height: 10,
                borderRadius: 5,
                background: color,
                border: `1px solid ${border}`,
              }}
            />
          );
        })}
      </div>
    </div>
  );
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

      {artifact.propensity_summary && (
        <PropensityBlock summary={artifact.propensity_summary} />
      )}

      {artifact.sensitivity_band && (
        <SensitivityBlock band={artifact.sensitivity_band} ci={artifact.ci} />
      )}

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
