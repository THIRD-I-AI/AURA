import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  dashboardService: { list: vi.fn(), create: vi.fn(), remove: vi.fn(), render: vi.fn() },
  savedQueryService: { list: vi.fn() },
}));

import { dashboardService, savedQueryService, type DashboardRender } from '../../../services/api';
import DashboardsPanel from '../DashboardsPanel';

const list = vi.mocked(dashboardService.list);
const create = vi.mocked(dashboardService.create);
const remove = vi.mocked(dashboardService.remove);
const renderDash = vi.mocked(dashboardService.render);
const listQueries = vi.mocked(savedQueryService.list);

const dash = (id: string, name: string) => ({ id, name, description: null, tiles: [{ id: 't1' }] }) as never;
const tile = (over: Record<string, unknown> = {}) => ({
  tile_id: 't1', saved_query_id: 'q1', title: 'Revenue', chart_type: 'table', status: 'success',
  columns: ['region', 'total'], rows: [['EU', 10], ['US', null]], row_count: 2, execution_time_ms: 1, ...over,
});
const rendering = (id: string, tiles: unknown[]): DashboardRender =>
  ({ success: true, dashboard_id: id, rendered_at: '2026-10-01T00:00:00Z', tiles }) as DashboardRender;

// BUG-257: the panel was list-only; create / render / delete had no UI.
describe('DashboardsPanel: create, open and delete dashboards (BUG-257)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue([]);
  });

  it('creates a dashboard with one tile per picked saved query', async () => {
    listQueries.mockResolvedValue([{ id: 'q1', name: 'Revenue' }, { id: 'q2', name: 'Churn' }] as never);
    create.mockResolvedValue(dash('d1', 'Weekly') as never);
    const user = userEvent.setup();
    render(<DashboardsPanel />);
    await screen.findByText('No dashboards yet');

    await user.click(screen.getByRole('button', { name: /new dashboard/i }));
    await user.type(screen.getByLabelText('Name'), 'Weekly');
    await user.click(await screen.findByLabelText('Churn'));
    list.mockResolvedValue([dash('d1', 'Weekly')]);
    await user.click(screen.getByRole('button', { name: 'Create dashboard' }));

    await waitFor(() => expect(create).toHaveBeenCalledWith({
      name: 'Weekly', description: undefined,
      tiles: [{ saved_query_id: 'q2', title: 'Churn', chart_type: 'table' }],
    }));
    expect(await screen.findByTestId('dashboard-tile')).toHaveTextContent('Weekly');
  });

  it('requires a name, and says so when saved queries cannot be loaded', async () => {
    listQueries.mockRejectedValue(new Error('down'));
    const user = userEvent.setup();
    render(<DashboardsPanel />);
    await screen.findByText('No dashboards yet');
    await user.click(screen.getByRole('button', { name: /new dashboard/i }));
    expect(await screen.findByText('Could not load saved queries.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create dashboard' }));
    expect(screen.getByText('A dashboard needs a name.')).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it('opens a dashboard and shows each tile honestly: rows, errors, missing queries, truncation', async () => {
    list.mockResolvedValue([dash('d1', 'Weekly')]);
    renderDash.mockResolvedValue(rendering('d1', [
      tile({ truncated: true }),
      tile({ tile_id: 't2', title: 'Broken', status: 'error', error: 'no such table', columns: [], rows: [] }),
      tile({ tile_id: 't3', title: 'Gone', status: 'missing', columns: [], rows: [] }),
    ]));
    const user = userEvent.setup();
    render(<DashboardsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Open Weekly' }));

    const ok = await screen.findByTestId('dashboard-render-tile-t1');
    expect(within(ok).getByRole('columnheader', { name: 'region' })).toBeInTheDocument();
    expect(within(ok).getByText('EU')).toBeInTheDocument();
    expect(within(ok).getByText('—')).toBeInTheDocument(); // null cell
    expect(within(ok).getByText(/showing the first 2 rows/i)).toBeInTheDocument();
    expect(screen.getByTestId('dashboard-render-tile-t2')).toHaveTextContent('no such table');
    expect(screen.getByTestId('dashboard-render-tile-t3')).toHaveTextContent(/no longer exists/);
  });

  it('a render failure is an error with Retry, not an empty dashboard', async () => {
    list.mockResolvedValue([dash('d1', 'Weekly')]);
    renderDash.mockRejectedValueOnce(new Error('500')).mockResolvedValueOnce(rendering('d1', [tile()]));
    const user = userEvent.setup();
    render(<DashboardsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Open Weekly' }));
    expect(await screen.findByText('Could not render "Weekly".')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('dashboard-render-tile-t1')).toBeInTheDocument();
  });

  it('a slower earlier open does not overwrite the dashboard opened after it', async () => {
    list.mockResolvedValue([dash('d1', 'First'), dash('d2', 'Second')]);
    let resolveFirst: (v: DashboardRender) => void = () => undefined;
    renderDash.mockImplementation((id: string) =>
      id === 'd1'
        ? new Promise<DashboardRender>((res) => { resolveFirst = res; })
        : Promise.resolve(rendering('d2', [tile({ tile_id: 'second-tile' })])));
    const user = userEvent.setup();
    render(<DashboardsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Open First' }));
    await user.click(screen.getByRole('button', { name: 'Open Second' }));
    expect(await screen.findByTestId('dashboard-render-tile-second-tile')).toBeInTheDocument();

    resolveFirst(rendering('d1', [tile({ tile_id: 'first-tile' })]));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('dashboard-render-tile-first-tile')).not.toBeInTheDocument();
    expect(screen.getByTestId('dashboard-render-tile-second-tile')).toBeInTheDocument();
  });

  it('deletes only after a confirming click', async () => {
    list.mockResolvedValue([dash('d1', 'Weekly')]);
    remove.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<DashboardsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Delete Weekly' }));
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('d1'));
  });
});
