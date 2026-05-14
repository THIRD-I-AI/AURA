import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import CounterfactualCard, {
  type CounterfactualOperatorView,
} from '../CounterfactualCard';

const baseFixture: CounterfactualOperatorView = {
  record_id: 'ca_test_1',
  headline: 'Counterfactual decrease of about -1.50 on monthly_revenue (confidence: high).',
  point_estimate: -1.5,
  ci: [-2.0, -1.0],
  confidence: 'high',
  top_challenges: [
    { text: 'n_samples is small (n=120)', severity: 'low' },
    {
      text: 'Treatment may be endogenous to undocumented promotion variable',
      severity: 'medium',
      suggested_check: 'add promotion as a parent of treatment',
    },
  ],
  audit_record_hash: '0xdead'.padEnd(64, '0'),
};

describe('CounterfactualCard', () => {
  it('renders the headline', () => {
    render(<CounterfactualCard artifact={baseFixture} />);
    expect(screen.getByText(/Counterfactual decrease/)).toBeInTheDocument();
  });

  it('renders the confidence badge with the matching label', () => {
    render(<CounterfactualCard artifact={baseFixture} />);
    expect(screen.getByTestId('confidence-badge').textContent).toBe('high');
  });

  it('renders the point estimate and CI', () => {
    render(<CounterfactualCard artifact={baseFixture} />);
    expect(screen.getByText(/Point estimate/)).toBeInTheDocument();
    // "-1.50" appears in both the headline and the estimate span — getAllByText
    // returns both; we just need it to be present at least once in the estimate
    // region. Match on the formatted CI string instead which is unique.
    expect(screen.getAllByText(/-1\.50/).length).toBeGreaterThan(0);
    expect(screen.getByText(/\[-2\.00, -1\.00\]/)).toBeInTheDocument();
  });

  it('hides challenges by default and reveals them on click', async () => {
    const user = userEvent.setup();
    render(<CounterfactualCard artifact={baseFixture} />);
    expect(screen.queryByText(/n_samples is small/)).not.toBeInTheDocument();
    await user.click(screen.getByText(/See the debate/i));
    expect(screen.getByText(/n_samples is small/)).toBeInTheDocument();
    expect(screen.getByText(/Treatment may be endogenous/)).toBeInTheDocument();
  });

  it('shows the suggested_check hint when present', async () => {
    const user = userEvent.setup();
    render(<CounterfactualCard artifact={baseFixture} />);
    await user.click(screen.getByText(/See the debate/i));
    expect(screen.getByText(/add promotion as a parent of treatment/)).toBeInTheDocument();
  });

  it('renders an empty-state message when challenges is empty', async () => {
    const user = userEvent.setup();
    render(<CounterfactualCard artifact={{ ...baseFixture, top_challenges: [] }} />);
    await user.click(screen.getByText(/See the debate/i));
    expect(screen.getByText(/No challenges raised/i)).toBeInTheDocument();
  });

  it('renders the truncated audit_record_hash', () => {
    const { container } = render(<CounterfactualCard artifact={baseFixture} />);
    // The Card slices the first 16 chars and appends an ellipsis. The
    // fixture is "0xdead" + 58 zero-padding → "0xdead0000000000" + "…".
    expect(container.textContent).toMatch(/audit_record_hash: 0xdead0000000000…/);
  });

  // ── Sprint 14: propensity block ─────────────────────────────────

  it('does not render the propensity block when summary is absent', () => {
    render(<CounterfactualCard artifact={baseFixture} />);
    expect(screen.queryByTestId('propensity-block')).not.toBeInTheDocument();
  });

  it('renders the propensity block with the fragility badge when summary present', () => {
    render(
      <CounterfactualCard
        artifact={{
          ...baseFixture,
          propensity_summary: {
            method: 'double_ml',
            fragility: 'ok',
            n_extreme: 0,
            n_total: 300,
            p05: 0.25, p25: 0.40, p50: 0.50, p75: 0.60, p95: 0.78,
            mean: 0.50,
          },
        }}
      />,
    );
    expect(screen.getByTestId('propensity-block')).toBeInTheDocument();
    expect(screen.getByTestId('propensity-fragility').textContent).toBe('propensity ok');
    expect(screen.getByTestId('propensity-band')).toBeInTheDocument();
    expect(screen.getByTestId('propensity-mean')).toBeInTheDocument();
    expect(screen.getByText(/0\/300 extreme \(0\.0%\)/)).toBeInTheDocument();
  });

  it('renders the fragility badge in red mode when fragility=red', () => {
    render(
      <CounterfactualCard
        artifact={{
          ...baseFixture,
          propensity_summary: {
            method: 'double_ml',
            fragility: 'red',
            n_extreme: 45,
            n_total: 300,
            p05: 0.04, p25: 0.20, p50: 0.50, p75: 0.80, p95: 0.97,
            mean: 0.50,
          },
        }}
      />,
    );
    expect(screen.getByTestId('propensity-fragility').textContent).toBe('IPW-fragile');
    expect(screen.getByText(/45\/300 extreme \(15\.0%\)/)).toBeInTheDocument();
  });

  // ── Sprint 14: sensitivity-band block ────────────────────────────

  it('does not render the sensitivity block when band is absent', () => {
    render(<CounterfactualCard artifact={baseFixture} />);
    expect(screen.queryByTestId('sensitivity-block')).not.toBeInTheDocument();
  });

  it('renders one dot per refuter in the sensitivity band', () => {
    render(
      <CounterfactualCard
        artifact={{
          ...baseFixture,
          sensitivity_band: {
            baseline: -1.5,
            perturbations: [
              { refuter: 'placebo', estimate_after: 0.02, passed: true },
              { refuter: 'random_common_cause', estimate_after: -1.48, passed: true },
              { refuter: 'data_subset', estimate_after: -1.52, passed: true },
              { refuter: 'sensitivity', estimate_after: -0.40, passed: false },
            ],
          },
        }}
      />,
    );
    expect(screen.getByTestId('sensitivity-block')).toBeInTheDocument();
    expect(screen.getByTestId('sensitivity-baseline')).toBeInTheDocument();
    expect(screen.getByTestId('sensitivity-dot-placebo')).toBeInTheDocument();
    expect(screen.getByTestId('sensitivity-dot-random_common_cause')).toBeInTheDocument();
    expect(screen.getByTestId('sensitivity-dot-data_subset')).toBeInTheDocument();
    expect(screen.getByTestId('sensitivity-dot-sensitivity')).toBeInTheDocument();
    // baseline line in the label
    expect(screen.getByText(/baseline -1\.50/)).toBeInTheDocument();
    expect(screen.getByText(/4 refuters/)).toBeInTheDocument();
  });
});
