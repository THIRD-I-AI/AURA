import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  analyticsService: {
    getQueryHistory: vi.fn(),
  },
}));

import { analyticsService } from '../../../services/api';
import QueryHistoryPanel from '../QueryHistoryPanel';

const getQueryHistory = analyticsService.getQueryHistory as ReturnType<typeof vi.fn>;

const queries = [
  { prompt: 'Total revenue by region', sql: 'select 1', status: 'success', row_count: 12, timestamp: '2026-08-20T09:00:00Z' },
  { prompt: 'Customer churn', sql: 'select 2', status: 'error', row_count: null, timestamp: '2026-08-21T09:00:00Z' },
];

describe('QueryHistoryPanel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders real query rows in the DataTable', async () => {
    getQueryHistory.mockResolvedValue({ success: true, total: 2, queries });
    render(<QueryHistoryPanel />);
    await waitFor(() => expect(screen.getByText('Total revenue by region')).toBeInTheDocument());
    expect(screen.getByText('Customer churn')).toBeInTheDocument();
    expect(screen.getByText('2 queries · this workspace')).toBeInTheDocument();
  });

  it('renders an honest empty state when there is no history', async () => {
    getQueryHistory.mockResolvedValue({ success: true, total: 0, queries: [] });
    render(<QueryHistoryPanel />);
    await waitFor(() => expect(screen.getByText('No queries yet')).toBeInTheDocument());
  });

  it('renders an honest error state when the gateway is unreachable', async () => {
    getQueryHistory.mockRejectedValue(new Error('network down'));
    render(<QueryHistoryPanel />);
    await waitFor(() => expect(screen.getByText(/could not reach the gateway to load query history/i)).toBeInTheDocument());
  });

  it('filters rows by the query text', async () => {
    getQueryHistory.mockResolvedValue({ success: true, total: 2, queries });
    render(<QueryHistoryPanel />);
    await waitFor(() => expect(screen.getByText('Total revenue by region')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Filter queries…'), 'churn');

    expect(screen.queryByText('Total revenue by region')).not.toBeInTheDocument();
    expect(screen.getByText('Customer churn')).toBeInTheDocument();
  });

  it('sorts by rows ascending/descending on header click', async () => {
    getQueryHistory.mockResolvedValue({ success: true, total: 2, queries });
    render(<QueryHistoryPanel />);
    await waitFor(() => expect(screen.getByText('Total revenue by region')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^rows/i }));

    const rowsInOrder = screen.getAllByRole('row').slice(1); // drop header row
    // ascending: the null/-1 row_count (Customer churn) sorts first
    expect(within(rowsInOrder[0]).getByText('Customer churn')).toBeInTheDocument();
  });

  it('opens the detail drawer with full row content on row click, and closes it', async () => {
    getQueryHistory.mockResolvedValue({ success: true, total: 2, queries });
    render(<QueryHistoryPanel />);
    await waitFor(() => expect(screen.getByText('Total revenue by region')).toBeInTheDocument());

    const user = userEvent.setup();
    expect(screen.queryByText('Query detail')).not.toBeInTheDocument();

    await user.click(screen.getByText('Total revenue by region'));

    const drawer = screen.getByRole('dialog');
    expect(within(drawer).getByText('Query detail')).toBeInTheDocument();
    expect(within(drawer).getByText('select 1')).toBeInTheDocument();
    expect(within(drawer).getByText('Total revenue by region')).toBeInTheDocument();
    expect(within(drawer).getByText('SUCCESS')).toBeInTheDocument();
    expect(within(drawer).getByText('12')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByText('Query detail')).not.toBeInTheDocument());
  });

  it('closes the detail drawer on Escape', async () => {
    getQueryHistory.mockResolvedValue({ success: true, total: 2, queries });
    render(<QueryHistoryPanel />);
    await waitFor(() => expect(screen.getByText('Total revenue by region')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByText('Customer churn'));
    expect(screen.getByText('Query detail')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByText('Query detail')).not.toBeInTheDocument());
  });
});

// BUG-394: the API's wire shape is {rows, executionTime}; the panel read
// row_count / execution_time_ms and always showed '—'.
describe('QueryHistoryPanel with the API wire shape', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('shows rows and execution time from `rows` / `executionTime`', async () => {
    getQueryHistory.mockResolvedValue({ success: true, total: 1, queries: [
      { id: 'q1', prompt: 'Orders by month', sql: 'select 3', status: 'success', rows: 37, executionTime: 12.5, timestamp: '2026-08-22T09:00:00Z' },
    ] });
    render(<QueryHistoryPanel />);
    await waitFor(() => expect(screen.getByText('Orders by month')).toBeInTheDocument());
    expect(screen.getByText('37')).toBeInTheDocument();
    await userEvent.setup().click(screen.getByText('Orders by month'));
    await waitFor(() => expect(screen.getByText('12.5 ms')).toBeInTheDocument());
  });
});
