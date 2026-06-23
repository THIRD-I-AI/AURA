import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { sendMessage, cockpit } = vi.hoisted(() => ({
  sendMessage: vi.fn(),
  cockpit: { activeDataset: 'sales.csv' as string | null, setActiveDataset: () => {} },
}));
vi.mock('../../services/api', () => ({ chatService: { sendMessage: (...a: unknown[]) => sendMessage(...a) } }));
vi.mock('../CockpitProvider', () => ({ useCockpit: () => cockpit }));

import QueryPanel from '../panels/QueryPanel';

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
