import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  savedQueryService: {
    list: vi.fn(),
    listRuns: vi.fn(),
    setSchedule: vi.fn(),
    clearSchedule: vi.fn(),
  },
}));

import { savedQueryService } from '../../../services/api';
import SchedulerPanel from '../SchedulerPanel';

const list = savedQueryService.list as ReturnType<typeof vi.fn>;
const listRuns = savedQueryService.listRuns as ReturnType<typeof vi.fn>;
const clearSchedule = savedQueryService.clearSchedule as ReturnType<typeof vi.fn>;

const scheduled = {
  id: 'q1', name: 'Daily revenue', sql: 'select 1', starred: false, created_at: '', updated_at: '',
  schedule: { interval: 'daily' as const, hour: 9, minute: 0, enabled: true },
};

describe('SchedulerPanel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders only real scheduled jobs from GET /saved-queries, filtering out unscheduled ones', async () => {
    list.mockResolvedValue([
      { ...scheduled, next_run_at: '2026-08-01T09:00:00Z' },
      { id: 'q2', name: 'Unscheduled query', sql: 'select 2', starred: false, created_at: '', updated_at: '', schedule: null },
    ]);
    render(<SchedulerPanel />);
    await waitFor(() => expect(screen.getByText('Daily revenue')).toBeInTheDocument());
    expect(screen.queryByText('Unscheduled query')).not.toBeInTheDocument();
    expect(screen.getByText(/daily at 09:00/)).toBeInTheDocument();
  });

  it('renders an honest empty state when nothing is scheduled', async () => {
    list.mockResolvedValue([]);
    render(<SchedulerPanel />);
    await waitFor(() => expect(screen.getByText(/no scheduled jobs/i)).toBeInTheDocument());
  });

  it('renders an honest error state when the gateway is unreachable', async () => {
    list.mockRejectedValue(new Error('network down'));
    render(<SchedulerPanel />);
    await waitFor(() => expect(screen.getByText(/could not reach the gateway to load scheduled jobs/i)).toBeInTheDocument());
  });

  it('loads and shows real run history on demand from GET /saved-queries/:id/runs', async () => {
    list.mockResolvedValue([scheduled]);
    listRuns.mockResolvedValue([
      { id: 'r1', started_at: '2026-07-30T09:00:00Z', completed_at: '2026-07-30T09:00:01Z', status: 'success', row_count: 12, execution_time_ms: 45 },
    ]);
    render(<SchedulerPanel />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText('Daily revenue')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /show runs/i }));
    await waitFor(() => expect(listRuns).toHaveBeenCalledWith('q1'));
    expect(await screen.findByText(/12 rows · 45ms/)).toBeInTheDocument();
  });

  it('BUG-327: re-opening a job and clicking Refresh fetch fresh run history', async () => {
    list.mockResolvedValue([scheduled]);
    listRuns
      .mockResolvedValueOnce([{ id: 'r1', started_at: '2026-07-30T09:00:00Z', completed_at: '2026-07-30T09:00:01Z', status: 'success', row_count: 12, execution_time_ms: 45 }])
      .mockResolvedValueOnce([{ id: 'r2', started_at: '2026-07-31T09:00:00Z', completed_at: '2026-07-31T09:00:01Z', status: 'success', row_count: 30, execution_time_ms: 50 }])
      .mockResolvedValueOnce([{ id: 'r3', started_at: '2026-08-01T09:00:00Z', completed_at: '2026-08-01T09:00:01Z', status: 'success', row_count: 41, execution_time_ms: 60 }]);
    render(<SchedulerPanel />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText('Daily revenue')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /show runs/i }));
    expect(await screen.findByText(/12 rows · 45ms/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /hide runs/i }));
    await user.click(screen.getByRole('button', { name: /show runs/i }));
    expect(await screen.findByText(/30 rows · 50ms/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /refresh/i }));
    expect(await screen.findByText(/41 rows · 60ms/)).toBeInTheDocument();
    expect(listRuns).toHaveBeenCalledTimes(3);
  });

  it('BUG-327: a failed run-history load can be retried by re-opening', async () => {
    list.mockResolvedValue([scheduled]);
    listRuns
      .mockRejectedValueOnce(new Error('timeout'))
      .mockResolvedValueOnce([{ id: 'r1', started_at: '2026-07-30T09:00:00Z', completed_at: '2026-07-30T09:00:01Z', status: 'success', row_count: 12, execution_time_ms: 45 }]);
    render(<SchedulerPanel />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText('Daily revenue')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /show runs/i }));
    expect(await screen.findByText(/could not load run history/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /hide runs/i }));
    await user.click(screen.getByRole('button', { name: /show runs/i }));

    expect(await screen.findByText(/12 rows · 45ms/)).toBeInTheDocument();
    expect(screen.queryByText(/could not load run history/i)).not.toBeInTheDocument();
  });

  it('removes a schedule via the verified DELETE endpoint, not a fabricated toggle', async () => {
    list.mockResolvedValue([scheduled]);
    clearSchedule.mockResolvedValue({ ...scheduled, schedule: null });
    render(<SchedulerPanel />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText('Daily revenue')).toBeInTheDocument());
    // BUG-253: the first click only arms the removal; it used to delete outright.
    await user.click(screen.getByRole('button', { name: /remove schedule/i }));
    expect(clearSchedule).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: /confirm remove/i }));
    await waitFor(() => expect(clearSchedule).toHaveBeenCalledWith('q1'));
  });

  it('BUG-253: cancelling a removal leaves the schedule alone', async () => {
    list.mockResolvedValue([scheduled]);
    render(<SchedulerPanel />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText('Daily revenue')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /remove schedule/i }));
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(clearSchedule).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /remove schedule/i })).toBeInTheDocument();
  });
});
