import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

const { sendMessage, cockpit } = vi.hoisted(() => ({
  sendMessage: vi.fn(),
  cockpit: { activeDataset: 'sales.csv' as string | null, setActiveDataset: () => {} },
}));
vi.mock('../../services/api', () => ({ chatService: { sendMessage: (...a: unknown[]) => sendMessage(...a) } }));
vi.mock('../CockpitProvider', () => ({ useCockpit: () => cockpit }));

import QueryPanel from '../panels/QueryPanel';

beforeAll(() => {
  // Recharts' ResponsiveContainer measures the parent via ResizeObserver,
  // which jsdom doesn't ship. Stub a minimal implementation.
  if (typeof ResizeObserver === 'undefined') {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

afterEach(() => {
  sendMessage.mockReset();
  cockpit.activeDataset = 'sales.csv';
});

describe('QueryPanel', () => {
  it('sends the prompt scoped to the active dataset and renders the SQL + rows', async () => {
    sendMessage.mockResolvedValue({
      job_id: 'j1', status: 'Success', final_query: 'SELECT 1',
      execution_result: { success: true, columns: ['n'], rows: [[1]] },
    });
    render(<QueryPanel api={{} as never} params={{} as never} containerApi={{} as never} />);
    fireEvent.change(screen.getByTestId('query-input'), { target: { value: 'total revenue' } });
    fireEvent.click(screen.getByTestId('query-run'));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith('total revenue', { uploadedFile: 'sales.csv' }));
    expect(await screen.findByText('SELECT 1')).toBeInTheDocument();
  });

  it('renders the chart from a real chart_spec (BUG-134: chart pipeline was wired end-to-end but never rendered)', async () => {
    sendMessage.mockResolvedValue({
      job_id: 'j2', status: 'Success', final_query: 'SELECT region, revenue FROM sales',
      execution_result: {
        success: true,
        columns: ['region', 'revenue'],
        rows: [['east', 10], ['west', 20]],
        data: [{ region: 'east', revenue: 10 }, { region: 'west', revenue: 20 }],
        chart_spec: { type: 'bar', x: 'region', y: 'revenue', title: 'Revenue by region' },
      },
    });
    render(<QueryPanel api={{} as never} params={{} as never} containerApi={{} as never} />);
    fireEvent.change(screen.getByTestId('query-input'), { target: { value: 'revenue by region' } });
    fireEvent.click(screen.getByTestId('query-run'));
    expect(await screen.findByText(/revenue by region/i)).toBeInTheDocument();
  });

  it('prompts to pick a dataset and does not query when none is active', async () => {
    cockpit.activeDataset = null;
    render(<QueryPanel api={{} as never} params={{} as never} containerApi={{} as never} />);
    expect(screen.getByTestId('query-context')).toHaveTextContent('No dataset selected');
    fireEvent.change(screen.getByTestId('query-input'), { target: { value: 'total revenue' } });
    fireEvent.click(screen.getByTestId('query-run'));
    expect(sendMessage).not.toHaveBeenCalled();
    expect(screen.getByText(/Select a dataset/i)).toBeInTheDocument();
  });
});
