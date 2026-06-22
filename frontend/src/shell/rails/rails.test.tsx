import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  savedQueryService: {
    list: vi.fn().mockResolvedValue([
      { id: '1', name: 'Revenue by region', sql: 'SELECT 1', starred: true, created_at: '', updated_at: '' },
    ]),
  },
  lineageService: {
    get: vi.fn().mockResolvedValue({
      success: true,
      nodes: [],
      edges: [],
      summary: { tables: 2, queries: 3, dashboards: 0, edges: 4 },
    }),
  },
}));
vi.mock('../../hooks/useSystemHealth', () => ({
  useSystemHealth: () => ({ isOnline: true, status: 'healthy' }),
  healthHint: () => 'Gateway healthy',
}));
vi.mock('../../store', () => ({
  useAuraStore: () => ({
    state: {
      queryHistory: [
        { id: 'q1', prompt: 'top vendors', sql: 'SELECT', status: 'success', rows: 5, executionTime: 12, timestamp: '' },
      ],
    },
    actions: { fetchQueryHistory: vi.fn() },
  }),
}));

import DashboardRail from './DashboardRail';
import HistoryRail from './HistoryRail';
import LineageRail from './LineageRail';

describe('rail slots', () => {
  it('DashboardRail shows pulse + recent saved query', async () => {
    render(<DashboardRail />);
    expect(screen.getByText('Gateway healthy')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Revenue by region/)).toBeInTheDocument());
  });

  it('HistoryRail shows a recent prompt', () => {
    render(<HistoryRail />);
    expect(screen.getByText(/top vendors/)).toBeInTheDocument();
  });

  it('LineageRail shows the graph summary', async () => {
    render(<LineageRail />);
    await waitFor(() => expect(screen.getByText(/tables/)).toBeInTheDocument());
    expect(screen.getByText('2')).toBeInTheDocument();
  });
});
