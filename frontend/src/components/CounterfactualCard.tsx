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

export interface CATEDistributionSummary {
  method: string;                              // e.g. 'forest_dr'
  quantiles: number[];                         // 10 evenly-spaced quantiles, p05..p95
  point: number;                               // ATE = mean of per-row CATEs
  ci_lower: number;
  ci_upper: number;
  idr: number;                                 // inter-decile spread = quantiles[9] - quantiles[0]
  heterogeneity: 'low' | 'moderate' | 'high';  // bucketed from |idr| / |point|
}

export interface CounterfactualOperatorView {
  record_id: string;
  headline: string;
  point_estimate: number;
  ci: [number, number];
  // Sprint 16 — which contract the CI carries:
  //   * "asymptotic"  — classical statsmodels / BLB interval
  //                     (Sprint 12-15 default). Coverage relies on
  //                     correctly-specified nuisance models +
  //                     large-sample asymptotics.
  //   * "conformal"   — split-conformal on AIPW pseudo-outcomes
  //                     (Lei & Candès 2021). Coverage holds at the
  //                     stated 1-alpha level in finite samples
  //                     regardless of nuisance-model misspecification.
  //   * "mixed"       — multiple estimators contributed and at least
  //                     one is conformal. Treat as "weakest contract"
  //                     and lean on the conformal badge meaning.
  // Optional for forward compat with pre-S16 artifacts.
  ci_method?: 'asymptotic' | 'conformal' | 'mixed';
  confidence: 'low' | 'medium' | 'high';
  top_challenges: CounterfactualChallenge[];
  audit_record_hash: string;
  // Sprint 14 additions — both optional so older artifacts that
  // pre-date the propensity work still render cleanly.
  propensity_summary?: PropensitySummary;
  sensitivity_band?: SensitivityBand;
  // Sprint 15 addition — only populated when ForestDR ran (non-
  // parametric CATE) and surfaces heterogeneity the linear stage
  // can't express.
  cate_distribution_summary?: CATEDistributionSummary;
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

// Heterogeneity (S15) reuses the same trustworthiness palette:
// `high` reads as "the population is meaningfully split" — same red
// vocabulary the operator already knows for fragility and low
// confidence. `low` reads as "one number is enough" — green.
const HETEROGENEITY_BG: Record<string, string> = {
  high:     CONFIDENCE_BG.low,
  moderate: CONFIDENCE_BG.medium,
  low:      CONFIDENCE_BG.high,
};
const HETEROGENEITY_FG: Record<string, string> = {
  high:     CONFIDENCE_FG.low,
  moderate: CONFIDENCE_FG.medium,
  low:      CONFIDENCE_FG.high,
};
const HETEROGENEITY_BORDER: Record<string, string> = {
  high:     CONFIDENCE_BORDER.low,
  moderate: CONFIDENCE_BORDER.medium,
  low:      CONFIDENCE_BORDER.high,
};
const HETEROGENEITY_LABEL: Record<string, string> = {
  high:     'heterogeneous',
  moderate: 'some heterogeneity',
  low:      'homogeneous',
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

// ── Sprint 15 — CATE distribution histogram ──────────────────────────
//
// Renders the per-row CATE distribution from a Forest-DR estimator as
// a 10-bar histogram, one bar per decile, with a baseline marker at
// the population mean (the ATE). When the inter-decile spread is much
// larger than the point, the heterogeneity badge goes red — that's
// the operator's signal that summarising the effect as one number is
// hiding a meaningfully split population.

const CATEDistributionBlock: React.FC<{
  summary: CATEDistributionSummary;
}> = ({ summary }) => {
  // Domain: the 10 quantiles tell us the range; pad slightly so the
  // edge bars don't sit flush against the container edges.
  const minQ = summary.quantiles[0];
  const maxQ = summary.quantiles[summary.quantiles.length - 1];
  const domainLo = Math.min(minQ, summary.point, 0);
  const domainHi = Math.max(maxQ, summary.point, 0);
  const span = Math.max(domainHi - domainLo, 1e-9);
  const meanPct = ((summary.point - domainLo) / span) * 100;
  const zeroPct = ((0 - domainLo) / span) * 100;

  const fg = HETEROGENEITY_FG[summary.heterogeneity];
  const border = HETEROGENEITY_BORDER[summary.heterogeneity];

  // Each bar represents the CATE value at one decile. We render the
  // bars side-by-side so the eye reads them as a CDF/histogram hybrid.
  const barWidthPct = 100 / summary.quantiles.length;

  return (
    <div data-testid="cate-distribution-block" style={{ marginTop: 10 }}>
      <div
        style={{
          fontSize: 12,
          color: 'var(--text-secondary, #cbd5e1)',
          display: 'flex',
          gap: 8,
          alignItems: 'center',
        }}
      >
        <span style={{ minWidth: 92 }}>CATE ({summary.method})</span>
        <span
          data-testid="cate-heterogeneity"
          style={{
            padding: '1px 6px',
            borderRadius: 3,
            fontSize: 10,
            background: HETEROGENEITY_BG[summary.heterogeneity],
            color: fg,
            border: `1px solid ${border}`,
          }}
        >
          {HETEROGENEITY_LABEL[summary.heterogeneity]}
        </span>
        <span style={{ fontFamily: 'monospace', opacity: 0.8 }}>
          spread {summary.idr.toFixed(2)}
        </span>
      </div>
      <div
        style={{
          marginTop: 4,
          position: 'relative',
          height: 36,
          background: 'rgba(148, 163, 184, 0.1)',
          borderRadius: 4,
          overflow: 'hidden',
        }}
      >
        {/* Zero reference line — when CATE crosses zero in either
            direction, this is the visual anchor for "no effect" */}
        {zeroPct >= 0 && zeroPct <= 100 && (
          <div
            data-testid="cate-zero-line"
            style={{
              position: 'absolute',
              left: `calc(${zeroPct}% - 1px)`,
              top: 0, bottom: 0, width: 2,
              background: 'var(--text-muted, #64748b)',
              opacity: 0.6,
            }}
          />
        )}
        {/* Per-decile bars laid out as a histogram. Each bar's height
            scales with the inverse rank — wider bars near the bulk of
            the distribution. (For 10 quantiles the bar heights are
            uniform; the visual is dominated by the horizontal
            position which carries the CATE value.) */}
        {summary.quantiles.map((q, i) => {
          const pct = ((q - domainLo) / span) * 100;
          return (
            <div
              key={i}
              data-testid={`cate-bar-${i}`}
              title={`p${(5 + i * 10).toString().padStart(2, '0')}: CATE=${q.toFixed(3)}`}
              style={{
                position: 'absolute',
                left: `calc(${pct}% - ${barWidthPct / 4}%)`,
                width: `${barWidthPct / 2}%`,
                top: 6,
                bottom: 6,
                background: fg,
                opacity: 0.85,
                borderRadius: 2,
              }}
            />
          );
        })}
        {/* ATE marker */}
        <div
          data-testid="cate-mean"
          style={{
            position: 'absolute',
            left: `calc(${meanPct}% - 1px)`,
            top: 0, bottom: 0,
            width: 2,
            background: 'var(--text-primary, #f1f5f9)',
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
        <span>{domainLo.toFixed(2)}</span>
        <span>ATE = {summary.point.toFixed(2)}</span>
        <span>{domainHi.toFixed(2)}</span>
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
        {artifact.ci_method && (
          <span
            data-testid="ci-method-badge"
            title={
              artifact.ci_method === 'conformal'
                ? 'Distribution-free finite-sample coverage (Lei & Candès 2021). Coverage holds regardless of nuisance-model misspecification.'
                : artifact.ci_method === 'mixed'
                ? 'Multiple estimators contributed; at least one shipped a conformal interval. Read as the weakest contract.'
                : 'Asymptotic-normal CI from statsmodels / BLB. Coverage requires correctly-specified nuisance and large-n asymptotics.'
            }
            style={{
              marginLeft: 8,
              padding: '1px 6px',
              borderRadius: 3,
              fontSize: 10,
              fontFamily: 'inherit',
              cursor: 'help',
              // Conformal = stronger contract = same green-ish tint
              // as high-confidence; asymptotic stays neutral.
              background:
                artifact.ci_method === 'conformal'
                  ? CONFIDENCE_BG.high
                  : artifact.ci_method === 'mixed'
                  ? CONFIDENCE_BG.medium
                  : 'rgba(148, 163, 184, 0.15)',
              color:
                artifact.ci_method === 'conformal'
                  ? CONFIDENCE_FG.high
                  : artifact.ci_method === 'mixed'
                  ? CONFIDENCE_FG.medium
                  : 'var(--text-muted, #94a3b8)',
              border:
                artifact.ci_method === 'conformal'
                  ? `1px solid ${CONFIDENCE_BORDER.high}`
                  : artifact.ci_method === 'mixed'
                  ? `1px solid ${CONFIDENCE_BORDER.medium}`
                  : '1px solid var(--border, #334155)',
            }}
          >
            {artifact.ci_method}
          </span>
        )}
      </div>

      {artifact.propensity_summary && (
        <PropensityBlock summary={artifact.propensity_summary} />
      )}

      {artifact.sensitivity_band && (
        <SensitivityBlock band={artifact.sensitivity_band} ci={artifact.ci} />
      )}

      {artifact.cate_distribution_summary && (
        <CATEDistributionBlock summary={artifact.cate_distribution_summary} />
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
