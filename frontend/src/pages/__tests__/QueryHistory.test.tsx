import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('recharts', () => ({
  PieChart: ({ children }: any) => <div data-testid="pie-chart">{children}</div>,
  Pie: () => null,
  Cell: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
}));

const mockFetchQueryHistory = vi.fn();

vi.mock('../../store', () => ({
  useAuraStore: vi.fn(() => ({
    state: {
      queryHistory: [
        {
          id: 'q1',
          prompt: 'Show revenue by product',
          sql: 'SELECT product, SUM(revenue) FROM sales GROUP BY product',
          status: 'success',
          rows: 5,
          execution_time_ms: 42,
          created_at: '2026-01-01T00:00:00Z',
        },
      ],
      queryHistoryLoading: false,
    },
    actions: {
      fetchQueryHistory: mockFetchQueryHistory,
      fetchStats: vi.fn(),
      fetchConnections: vi.fn(),
      loadFilesFromStorage: vi.fn(),
    },
  })),
}));

import QueryHistory from '../QueryHistory';

describe('QueryHistory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the page heading', () => {
    render(<QueryHistory />);
    expect(screen.getByText('Query History')).toBeInTheDocument();
  });

  it('renders KPI stats', () => {
    render(<QueryHistory />);
    expect(screen.getByText('Total Queries')).toBeInTheDocument();
    expect(screen.getByText('Success Rate')).toBeInTheDocument();
  });

  it('renders query items', () => {
    render(<QueryHistory />);
    expect(screen.getByText('Show revenue by product')).toBeInTheDocument();
  });

  it('renders filter buttons', () => {
    render(<QueryHistory />);
    expect(screen.getByText('All')).toBeInTheDocument();
  });
});
