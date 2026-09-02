import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  connectorService: {
    listSources: vi.fn(),
    getSchema: vi.fn(),
    syncTable: vi.fn(),
  },
}));

import { connectorService } from '../../../services/api';
import ConnectorsPanel from '../ConnectorsPanel';

const listSources = connectorService.listSources as ReturnType<typeof vi.fn>;
const getSchema = connectorService.getSchema as ReturnType<typeof vi.fn>;
const syncTable = connectorService.syncTable as ReturnType<typeof vi.fn>;

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

  describe('Sync to chat', () => {
    it('opens the table picker and calls schema then sync in sequence, showing the row count on success', async () => {
      listSources.mockResolvedValue(data);
      getSchema.mockResolvedValue({ orders: ['id', 'total'], customers: ['id', 'name'] });
      syncTable.mockResolvedValue({
        success: true,
        connection_id: 'c1',
        table_name: 'orders',
        file_name: 'warehouse__orders.parquet',
        row_count: 1234,
        synced_at: '2026-08-30T00:00:00Z',
      });

      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await waitFor(() => expect(screen.getByText('Warehouse')).toBeInTheDocument());

      await user.click(screen.getByTestId('wb-connector-sync-open-c1'));

      await waitFor(() => expect(getSchema).toHaveBeenCalledWith('c1'));
      expect(syncTable).not.toHaveBeenCalled();

      const select = await screen.findByLabelText('Table to sync from Warehouse');
      expect(select).toBeInTheDocument();
      await user.selectOptions(select, 'orders');

      await user.click(screen.getByRole('button', { name: 'Sync' }));

      await waitFor(() => expect(syncTable).toHaveBeenCalledWith('c1', 'orders'));
      // The two endpoints fire in sequence — schema list first, then sync.
      expect(getSchema.mock.invocationCallOrder[0]).toBeLessThan(syncTable.mock.invocationCallOrder[0]);

      await waitFor(() => expect(screen.getByTestId('wb-connector-sync-result-c1')).toHaveTextContent(/synced 1,234 rows from "orders"/i));
      expect(screen.getByTestId('wb-connector-sync-result-c1')).toHaveTextContent(/ask aura can now query this table/i);
    });

    it('shows a real disabled/loading state on the sync button while syncing', async () => {
      listSources.mockResolvedValue(data);
      getSchema.mockResolvedValue({ orders: ['id'] });
      let resolveSync: (v: unknown) => void = () => {};
      syncTable.mockReturnValue(new Promise((resolve) => { resolveSync = resolve; }));

      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await waitFor(() => expect(screen.getByText('Warehouse')).toBeInTheDocument());
      await user.click(screen.getByTestId('wb-connector-sync-open-c1'));
      await screen.findByLabelText('Table to sync from Warehouse');

      await user.click(screen.getByRole('button', { name: 'Sync' }));

      expect(screen.getByRole('button', { name: 'Syncing…' })).toBeDisabled();

      resolveSync({ success: true, connection_id: 'c1', table_name: 'orders', file_name: 'x.parquet', row_count: 5, synced_at: 'now' });
      await waitFor(() => expect(screen.getByTestId('wb-connector-sync-result-c1')).toBeInTheDocument());
    });

    it('shows an inline error when the schema fetch fails', async () => {
      listSources.mockResolvedValue(data);
      getSchema.mockRejectedValue(new Error('boom'));

      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await waitFor(() => expect(screen.getByText('Warehouse')).toBeInTheDocument());
      await user.click(screen.getByTestId('wb-connector-sync-open-c1'));

      await waitFor(() => expect(screen.getByText(/could not load tables for "warehouse"/i)).toBeInTheDocument());
      expect(syncTable).not.toHaveBeenCalled();
    });

    it('shows an inline error when the sync call fails', async () => {
      listSources.mockResolvedValue(data);
      getSchema.mockResolvedValue({ orders: ['id'] });
      syncTable.mockRejectedValue(new Error('sync failed'));

      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await waitFor(() => expect(screen.getByText('Warehouse')).toBeInTheDocument());
      await user.click(screen.getByTestId('wb-connector-sync-open-c1'));
      await screen.findByLabelText('Table to sync from Warehouse');

      await user.click(screen.getByRole('button', { name: 'Sync' }));

      await waitFor(() => expect(screen.getByText(/could not sync "orders" from "warehouse"/i)).toBeInTheDocument());
    });
  });
});
