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

  it('removes a schedule via the verified DELETE endpoint, not a fabricated toggle', async () => {
    list.mockResolvedValue([scheduled]);
    clearSchedule.mockResolvedValue({ ...scheduled, schedule: null });
    render(<SchedulerPanel />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText('Daily revenue')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /remove schedule/i }));
    await waitFor(() => expect(clearSchedule).toHaveBeenCalledWith('q1'));
  });
});
