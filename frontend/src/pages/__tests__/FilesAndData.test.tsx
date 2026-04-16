import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('recharts', () => ({
  BarChart: ({ children }: any) => <div data-testid="bar-chart">{children}</div>,
  Bar: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
  Cell: () => null,
}));

const mockFiles = [
  {
    id: '1',
    name: 'sales.csv',
    filename: 'sales.csv',
    sizeBytes: 1024,
    rows: 100,
    columns: 5,
    columnNames: ['id', 'name', 'price', 'qty', 'date'],
    uploadedAt: '2026-01-01T00:00:00Z',
    status: 'ready' as const,
  },
];

const mockActions = {
  loadFilesFromStorage: vi.fn(),
  fetchStats: vi.fn(),
  fetchConnections: vi.fn(),
  fetchQueryHistory: vi.fn(),
};

vi.mock('../../store', () => ({
  useAuraStore: vi.fn(() => ({
    state: { files: mockFiles },
    actions: mockActions,
  })),
}));

import FilesAndData from '../FilesAndData';

describe('FilesAndData', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders KPI cards', () => {
    render(<FilesAndData />);
    expect(screen.getByText('Datasets')).toBeInTheDocument();
    expect(screen.getByText('Total Rows')).toBeInTheDocument();
    expect(screen.getByText('Storage')).toBeInTheDocument();
  });

  it('renders file list when files exist', () => {
    render(<FilesAndData />);
    expect(screen.getByText('sales.csv')).toBeInTheDocument();
  });

  it('calls loadFilesFromStorage on mount', () => {
    render(<FilesAndData />);
    expect(mockActions.loadFilesFromStorage).toHaveBeenCalled();
  });
});
