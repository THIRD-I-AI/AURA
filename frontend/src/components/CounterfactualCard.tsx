import React, { useState } from 'react';

import { Button } from '@/components/ui-kit/button';
import { Card } from '@/components/ui-kit/card';
import { cn } from '@/lib/cn';

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
  // DSR-008 / BUG-153 — estimators that ran as a weaker fallback (double_ml
  // -> linear regression when econml is missing) yet still fed the headline
  // number. Absent when nothing degraded.
  degraded_methods?: string[];
  // Sprint 14 additions — both optional so older artifacts that
  // pre-date the propensity work still render cleanly.
  propensity_summary?: PropensitySummary;
  sensitivity_band?: SensitivityBand;
  // Sprint 15 addition — only populated when ForestDR ran (non-
  // parametric CATE) and surfaces heterogeneity the linear stage
  // can't express.
  cate_distribution_summary?: CATEDistributionSummary;
}

// Trust palette on design tokens (danger = fragile, warn = caution, signal =
// healthy). Tailwind needs whole class strings at build time, so each tone is
// spelled out rather than assembled from a color name.
const TONE_BADGE = {
  danger: 'border-danger/50 bg-danger/15 text-danger',
  warn: 'border-warn/50 bg-warn/15 text-warn',
  signal: 'border-signal/50 bg-signal/15 text-signal',
  neutral: 'border-border-hairline bg-raised text-text-tertiary',
} as const;
const TONE_FILL = {
  danger: 'bg-danger',
  warn: 'bg-warn',
  signal: 'bg-signal',
} as const;
const TONE_BAND = {
  danger: 'border-danger/50 bg-danger/15',
  warn: 'border-warn/50 bg-warn/15',
  signal: 'border-signal/50 bg-signal/15',
} as const;
type Tone = keyof typeof TONE_FILL;

const CONFIDENCE_TONE: Record<string, Tone> = {
  low: 'danger',
  medium: 'warn',
  high: 'signal',
};

// Reuses the confidence palette: red = fragile, amber = caution, ok = healthy.
// Mapping is deliberate — the operator already reads red badges as
// "trustworthiness problem" so the propensity badge speaks the same vocabulary.
const FRAGILITY_TONE: Record<string, Tone> = {
  red: 'danger',
  amber: 'warn',
  ok: 'signal',
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
const HETEROGENEITY_TONE: Record<string, Tone> = {
  high: 'danger',
  moderate: 'warn',
  low: 'signal',
};
const HETEROGENEITY_LABEL: Record<string, string> = {
  high:     'heterogeneous',
  moderate: 'some heterogeneity',
  low:      'homogeneous',
};

const BADGE_BASE = 'border px-1.5 py-px text-2xs';

// ── Sprint 14 — propensity quantile bar ──────────────────────────────
//
// Renders a thin horizontal bar from 0 to 1 with a shaded band covering
// [p05, p95] (the central 90% of the propensity distribution) and a
// tick at the mean. When the fragility is non-ok, the bar carries a
// badge with the n_extreme fraction. The visual maps the math: a
// fragile estimate is one whose band crosses or hugs the 0/1 boundary,
// and the eye can see that without reading the numbers.
//
// Inline `style` below is reserved for data-driven geometry (percent
// offsets computed from the estimate), which no static utility can express.

const PropensityBlock: React.FC<{ summary: PropensitySummary }> = ({ summary }) => {
  const extremeFrac = summary.n_total > 0 ? summary.n_extreme / summary.n_total : 0;
  // Clamp to [0, 1] for the visual; the math allows quantiles outside
  // [0, 1] in pathological fits but we don't want CSS overflows.
  const left = Math.max(0, Math.min(1, summary.p05)) * 100;
  const right = Math.max(0, Math.min(1, summary.p95)) * 100;
  const mean = Math.max(0, Math.min(1, summary.mean)) * 100;
  const tone = FRAGILITY_TONE[summary.fragility];

  return (
    <div data-testid="propensity-block" className="mt-2.5">
      <div className="flex items-center gap-2 text-xs text-text-secondary">
        <span className="min-w-23">Propensity ({summary.method})</span>
        <span
          data-testid="propensity-fragility"
          className={cn(BADGE_BASE, TONE_BADGE[tone])}
        >
          {FRAGILITY_LABEL[summary.fragility]}
        </span>
        <span className="font-mono opacity-80">
          {summary.n_extreme}/{summary.n_total} extreme ({(extremeFrac * 100).toFixed(1)}%)
        </span>
      </div>
      <div className="relative mt-1 h-2 overflow-hidden bg-raised">
        {/* Central 90% (p05 → p95) band */}
        <div
          data-testid="propensity-band"
          className={cn('absolute inset-y-0 border-x-2', TONE_BAND[tone])}
          style={{ left: `${left}%`, width: `${Math.max(0, right - left)}%` }}
        />
        {/* Mean tick */}
        <div
          data-testid="propensity-mean"
          className={cn('absolute inset-y-0 w-0.5', TONE_FILL[tone])}
          style={{ left: `calc(${mean}% - 1px)` }}
        />
      </div>
      <div className="mt-0.5 flex justify-between font-mono text-2xs text-text-tertiary">
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
    <div data-testid="sensitivity-block" className="mt-2.5">
      <div className="flex gap-2 text-xs text-text-secondary">
        <span className="min-w-23">Sensitivity</span>
        <span className="font-mono opacity-80">
          baseline {band.baseline.toFixed(2)} · {band.perturbations.length} refuters
        </span>
      </div>
      <div className="relative mt-1 h-4.5 bg-raised">
        {/* Baseline marker */}
        <div
          data-testid="sensitivity-baseline"
          className="absolute inset-y-0 w-0.5 bg-text-primary opacity-50"
          style={{ left: `calc(${baselinePct}% - 1px)` }}
        />
        {band.perturbations.map(p => {
          const pct = ((p.estimate_after - minV) / span) * 100;
          const tone: Tone = p.passed ? 'signal' : 'warn';
          return (
            <div
              key={p.refuter}
              data-testid={`sensitivity-dot-${p.refuter}`}
              title={`${p.refuter}: ${p.estimate_after.toFixed(3)} ${p.passed ? '(passed)' : '(failed)'}`}
              className={cn('absolute top-1 size-2.5 border', TONE_FILL[tone], TONE_BAND[tone])}
              style={{ left: `calc(${pct}% - 5px)` }}
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

  const tone = HETEROGENEITY_TONE[summary.heterogeneity];

  // Each bar represents the CATE value at one decile. We render the
  // bars side-by-side so the eye reads them as a CDF/histogram hybrid.
  const barWidthPct = 100 / summary.quantiles.length;

  return (
    <div data-testid="cate-distribution-block" className="mt-2.5">
      <div className="flex items-center gap-2 text-xs text-text-secondary">
        <span className="min-w-23">CATE ({summary.method})</span>
        <span
          data-testid="cate-heterogeneity"
          className={cn(BADGE_BASE, TONE_BADGE[tone])}
        >
          {HETEROGENEITY_LABEL[summary.heterogeneity]}
        </span>
        <span className="font-mono opacity-80">
          spread {summary.idr.toFixed(2)}
        </span>
      </div>
      <div className="relative mt-1 h-9 overflow-hidden bg-raised">
        {/* Zero reference line — when CATE crosses zero in either
            direction, this is the visual anchor for "no effect" */}
        {zeroPct >= 0 && zeroPct <= 100 && (
          <div
            data-testid="cate-zero-line"
            className="absolute inset-y-0 w-0.5 bg-text-tertiary opacity-60"
            style={{ left: `calc(${zeroPct}% - 1px)` }}
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
              className={cn('absolute inset-y-1.5 opacity-85', TONE_FILL[tone])}
              style={{
                left: `calc(${pct}% - ${barWidthPct / 4}%)`,
                width: `${barWidthPct / 2}%`,
              }}
            />
          );
        })}
        {/* ATE marker */}
        <div
          data-testid="cate-mean"
          className="absolute inset-y-0 w-0.5 bg-text-primary"
          style={{ left: `calc(${meanPct}% - 1px)` }}
        />
      </div>
      <div className="mt-0.5 flex justify-between font-mono text-2xs text-text-tertiary">
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

// Conformal = stronger contract = same green tint as high-confidence;
// asymptotic stays neutral.
const CI_METHOD_BADGE: Record<NonNullable<CounterfactualOperatorView['ci_method']>, string> = {
  conformal: TONE_BADGE.signal,
  mixed: TONE_BADGE.warn,
  asymptotic: TONE_BADGE.neutral,
};

const CounterfactualCard: React.FC<Props> = ({ artifact }) => {
  const [showDebate, setShowDebate] = useState(false);

  return (
    <Card
      data-testid="counterfactual-card"
      className="my-3 gap-0 px-4 shadow-none"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="m-0 font-display text-base font-medium text-text-primary">
          {artifact.headline}
        </h3>
        <span
          data-testid="confidence-badge"
          className={cn(
            'border px-2 py-0.5 text-xs whitespace-nowrap',
            TONE_BADGE[CONFIDENCE_TONE[artifact.confidence]],
          )}
        >
          {artifact.confidence}
        </span>
      </div>

      <div className="mt-2 text-[13px] text-text-secondary">
        Point estimate{' '}
        <span className="font-mono">{artifact.point_estimate.toFixed(2)}</span>
        {' '}· 95% CI{' '}
        <span className="font-mono">
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
            className={cn('ml-2 cursor-help', BADGE_BASE, CI_METHOD_BADGE[artifact.ci_method])}
          >
            {artifact.ci_method}
          </span>
        )}
      </div>

      {artifact.degraded_methods && artifact.degraded_methods.length > 0 && (
        <div
          data-testid="degraded-notice"
          role="note"
          className="mt-2 border border-warn/50 bg-warn/10 px-2 py-1 text-xs text-warn"
        >
          Degraded estimator: <span className="font-mono">{artifact.degraded_methods.join(', ')}</span>{' '}
          ran as a weaker linear-regression fallback because econml is unavailable, so the
          headline is not from the doubly-robust DR-Learner.
        </div>
      )}

      {artifact.propensity_summary && (
        <PropensityBlock summary={artifact.propensity_summary} />
      )}

      {artifact.sensitivity_band && (
        <SensitivityBlock band={artifact.sensitivity_band} ci={artifact.ci} />
      )}

      {artifact.cate_distribution_summary && (
        <CATEDistributionBlock summary={artifact.cate_distribution_summary} />
      )}

      <Button
        type="button"
        variant="link"
        size="xs"
        onClick={() => setShowDebate(s => !s)}
        className="mt-3 h-auto self-start rounded-none p-0 text-xs underline"
      >
        {showDebate ? 'Hide the debate' : 'See the debate'}
      </Button>

      {showDebate && (
        <ul className="mt-2 flex list-none flex-col gap-1.5 pl-0">
          {artifact.top_challenges.length === 0 && (
            <li className="text-[13px] italic text-text-secondary">
              No challenges raised — refutation tests passed and the critic had no objections.
            </li>
          )}
          {artifact.top_challenges.map((c, i) => (
            <li key={i} className="text-[13px] text-text-secondary">
              <span
                className={cn(
                  'mr-2 inline-block',
                  BADGE_BASE,
                  TONE_BADGE[CONFIDENCE_TONE[c.severity]],
                )}
              >
                {c.severity}
              </span>
              {c.text}
              {c.suggested_check && (
                <div className="ml-9 mt-0.5 text-xs opacity-70">
                  → {c.suggested_check}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 font-mono text-2xs text-text-tertiary">
        audit_record_hash: {artifact.audit_record_hash.slice(0, 16)}…
      </div>
    </Card>
  );
};

export default CounterfactualCard;
