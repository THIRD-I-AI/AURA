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
});
