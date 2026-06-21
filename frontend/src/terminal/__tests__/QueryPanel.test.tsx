import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const sendMessage = vi.fn();
vi.mock('../../services/api', () => ({ chatService: { sendMessage: (...a: unknown[]) => sendMessage(...a) } }));
vi.mock('../CockpitProvider', () => ({ useCockpit: () => ({ activeDataset: 'sales.csv', setActiveDataset: () => {} }) }));

import QueryPanel from '../panels/QueryPanel';

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
});
