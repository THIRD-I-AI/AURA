import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  lineageService: {
    get: vi.fn(),
  },
}));

import { lineageService } from '../../../services/api';
import LineagePanel from '../LineagePanel';

const get = lineageService.get as ReturnType<typeof vi.fn>;

const graph = {
  nodes: [
    { id: 'n1', type: 'table' as const, label: 'orders' },
    { id: 'n2', type: 'dashboard' as const, label: 'revenue_dashboard' },
  ],
  edges: [
    { id: 'e1', source: 'n1', target: 'n2' },
    { id: 'e2', source: 'n1', target: 'n2' },
  ],
  summary: { tables: 1, queries: 0, dashboards: 1, edges: 2 },
};

describe('LineagePanel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders real lineage rows in the DataTable', async () => {
    get.mockResolvedValue(graph);
    render(<LineagePanel />);
    await waitFor(() => expect(screen.getByText('orders')).toBeInTheDocument());
    expect(screen.getByText('revenue_dashboard')).toBeInTheDocument();
    expect(screen.getByText('2 nodes · 2 edges · provenance graph')).toBeInTheDocument();
  });

  it('renders an em-dash for a node with no downstream edges', async () => {
    get.mockResolvedValue(graph);
    render(<LineagePanel />);
    await waitFor(() => expect(screen.getByText('revenue_dashboard')).toBeInTheDocument());

    const dashboardRow = screen.getByText('revenue_dashboard').closest('tr')!;
    expect(within(dashboardRow).getByText('—')).toBeInTheDocument();
  });

  it('renders the derived downstream count for a node with outgoing edges', async () => {
    get.mockResolvedValue(graph);
    render(<LineagePanel />);
    await waitFor(() => expect(screen.getByText('orders')).toBeInTheDocument());

    const ordersRow = screen.getByText('orders').closest('tr')!;
    expect(within(ordersRow).getByText('2 →')).toBeInTheDocument();
  });

  it('renders an honest empty state when there is no lineage', async () => {
    get.mockResolvedValue({ nodes: [], edges: [], summary: { tables: 0, queries: 0, dashboards: 0, edges: 0 } });
    render(<LineagePanel />);
    await waitFor(() => expect(screen.getByText('No lineage yet')).toBeInTheDocument());
  });

  it('renders an honest error state when the gateway is unreachable', async () => {
    get.mockRejectedValue(new Error('network down'));
    render(<LineagePanel />);
    await waitFor(() => expect(screen.getByText(/could not reach the gateway to load lineage/i)).toBeInTheDocument());
  });

  it('filters rows by node label', async () => {
    get.mockResolvedValue(graph);
    render(<LineagePanel />);
    await waitFor(() => expect(screen.getByText('orders')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Filter lineage…'), 'revenue');

    expect(screen.queryByText('orders')).not.toBeInTheDocument();
    expect(screen.getByText('revenue_dashboard')).toBeInTheDocument();
  });

  it('sorts by downstream count ascending/descending on header click', async () => {
    get.mockResolvedValue(graph);
    render(<LineagePanel />);
    await waitFor(() => expect(screen.getByText('orders')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^downstream/i }));

    const rowsInOrder = screen.getAllByRole('row').slice(1); // drop header row
    // ascending: revenue_dashboard's 0 downstream sorts first
    expect(within(rowsInOrder[0]).getByText('revenue_dashboard')).toBeInTheDocument();
  });
});
