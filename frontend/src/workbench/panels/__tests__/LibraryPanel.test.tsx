import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  savedQueryService: {
    list: vi.fn(),
  },
}));

import { savedQueryService } from '../../../services/api';
import LibraryPanel from '../LibraryPanel';

const list = savedQueryService.list as ReturnType<typeof vi.fn>;

const queries = [
  { id: 'q1', name: 'Monthly revenue', sql: 'SELECT sum(amount) FROM sales', prompt: 'Monthly revenue', starred: false },
  { id: 'q2', name: 'Top customers', sql: 'SELECT customer, sum(amount) FROM sales GROUP BY customer', prompt: 'Who are our top customers?', starred: true },
];

describe('LibraryPanel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders real saved queries in the DataTable', async () => {
    list.mockResolvedValue(queries);
    render(<LibraryPanel />);
    await waitFor(() => expect(screen.getByText('Monthly revenue')).toBeInTheDocument());
    expect(screen.getByText('Top customers')).toBeInTheDocument();
    expect(screen.getByText('2 saved queries · 1 starred')).toBeInTheDocument();
  });

  it('renders an honest empty state when there are no saved queries', async () => {
    list.mockResolvedValue([]);
    render(<LibraryPanel />);
    await waitFor(() => expect(screen.getByText('No saved queries yet')).toBeInTheDocument());
  });

  it('renders an honest error state when the gateway is unreachable', async () => {
    list.mockRejectedValue(new Error('network down'));
    render(<LibraryPanel />);
    await waitFor(() => expect(screen.getByText(/could not reach the gateway to load the query library/i)).toBeInTheDocument());
  });

  it('filters rows by name/prompt text', async () => {
    list.mockResolvedValue(queries);
    render(<LibraryPanel />);
    await waitFor(() => expect(screen.getByText('Monthly revenue')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Filter library…'), 'customers');

    expect(screen.queryByText('Monthly revenue')).not.toBeInTheDocument();
    expect(screen.getByText('Top customers')).toBeInTheDocument();
  });

  it('sorts starred rows first on header click', async () => {
    list.mockResolvedValue(queries);
    render(<LibraryPanel />);
    await waitFor(() => expect(screen.getByText('Monthly revenue')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^starred/i }));
    await user.click(screen.getByRole('button', { name: /^starred/i })); // desc: starred (1) first

    const rowsInOrder = screen.getAllByRole('row').slice(1); // drop header row
    expect(within(rowsInOrder[0]).getByText('Top customers')).toBeInTheDocument();
  });
});
