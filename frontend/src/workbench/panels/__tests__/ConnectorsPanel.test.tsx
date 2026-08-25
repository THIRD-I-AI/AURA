import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  connectorService: {
    listSources: vi.fn(),
  },
}));

import { connectorService } from '../../../services/api';
import ConnectorsPanel from '../ConnectorsPanel';

const listSources = connectorService.listSources as ReturnType<typeof vi.fn>;

const data = {
  connections: [
    { id: 'c1', name: 'Warehouse', type: 'postgres', status: 'connected' },
    { id: 'c2', name: 'Legacy MySQL', type: 'mysql', status: 'disconnected' },
  ],
  count: 2,
  file_sources: 3,
};

describe('ConnectorsPanel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders real connection rows in the DataTable', async () => {
    listSources.mockResolvedValue(data);
    render(<ConnectorsPanel />);
    await waitFor(() => expect(screen.getByText('Warehouse')).toBeInTheDocument());
    expect(screen.getByText('Legacy MySQL')).toBeInTheDocument();
    expect(screen.getByText('2 database connections · 3 file sources')).toBeInTheDocument();
  });

  it('renders an honest empty state when there are no connections', async () => {
    listSources.mockResolvedValue({ connections: [], count: 0, file_sources: 0 });
    render(<ConnectorsPanel />);
    await waitFor(() => expect(screen.getByText('No connections yet')).toBeInTheDocument());
  });

  it('renders an honest error state when the gateway is unreachable', async () => {
    listSources.mockRejectedValue(new Error('network down'));
    render(<ConnectorsPanel />);
    await waitFor(() => expect(screen.getByText(/could not reach the gateway to list connectors/i)).toBeInTheDocument());
  });

  it('filters rows by name/type/status text', async () => {
    listSources.mockResolvedValue(data);
    render(<ConnectorsPanel />);
    await waitFor(() => expect(screen.getByText('Warehouse')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Filter connections…'), 'mysql');

    expect(screen.queryByText('Warehouse')).not.toBeInTheDocument();
    expect(screen.getByText('Legacy MySQL')).toBeInTheDocument();
  });

  it('sorts by name ascending/descending on header click', async () => {
    listSources.mockResolvedValue(data);
    render(<ConnectorsPanel />);
    await waitFor(() => expect(screen.getByText('Warehouse')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^name/i }));

    const rowsInOrder = screen.getAllByRole('row').slice(1); // drop header row
    // ascending: "Legacy MySQL" sorts before "Warehouse"
    expect(rowsInOrder[0]).toHaveTextContent('Legacy MySQL');
  });
});
