import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  costService: {
    breakdown: vi.fn(),
  },
}));

import { costService } from '../../../services/api';
import CostPanel from '../CostPanel';

const breakdown = costService.breakdown as ReturnType<typeof vi.fn>;

const data = {
  available: true,
  rows: [
    { provider: 'openai', model: 'gpt-4o', kind: 'prompt', tokens: 12000 },
    { provider: 'anthropic', model: 'claude-sonnet-5', kind: 'completion', tokens: 500 },
  ],
  totals: { prompt: 12000, completion: 500, cached_completion: 0 },
};

describe('CostPanel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders real usage rows in the DataTable', async () => {
    breakdown.mockResolvedValue(data);
    render(<CostPanel />);
    await waitFor(() => expect(screen.getByText('openai')).toBeInTheDocument());
    expect(screen.getByText('anthropic')).toBeInTheDocument();
    expect(screen.getByText('LLM token accounting · live')).toBeInTheDocument();
  });

  it('renders an honest empty state when there is no usage', async () => {
    breakdown.mockResolvedValue({ available: true, rows: [], totals: { prompt: 0, completion: 0, cached_completion: 0 } });
    render(<CostPanel />);
    await waitFor(() => expect(screen.getByText('No usage yet')).toBeInTheDocument());
  });

  it('renders an honest error state when the gateway is unreachable', async () => {
    breakdown.mockRejectedValue(new Error('network down'));
    render(<CostPanel />);
    await waitFor(() => expect(screen.getByText(/could not reach the gateway to load token accounting/i)).toBeInTheDocument());
  });

  it('filters rows by provider/model text', async () => {
    breakdown.mockResolvedValue(data);
    render(<CostPanel />);
    await waitFor(() => expect(screen.getByText('openai')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Filter usage…'), 'anthropic');

    expect(screen.queryByText('openai')).not.toBeInTheDocument();
    expect(screen.getByText('anthropic')).toBeInTheDocument();
  });

  it('sorts by tokens ascending/descending on header click', async () => {
    breakdown.mockResolvedValue(data);
    render(<CostPanel />);
    await waitFor(() => expect(screen.getByText('openai')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^tokens/i }));

    const rowsInOrder = screen.getAllByRole('row').slice(1); // drop header row
    // ascending: anthropic's 500 tokens sorts first
    expect(within(rowsInOrder[0]).getByText('anthropic')).toBeInTheDocument();
  });
});
