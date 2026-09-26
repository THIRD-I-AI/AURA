import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  connectorService: {
    listSources: vi.fn(),
    getSchema: vi.fn(),
    syncTable: vi.fn(),
    registry: vi.fn(),
    registerSource: vi.fn(),
    testConnection: vi.fn(),
  },
}));

import { connectorService } from '../../../services/api';
import ConnectorsPanel from '../ConnectorsPanel';

const listSources = connectorService.listSources as ReturnType<typeof vi.fn>;
const getSchema = connectorService.getSchema as ReturnType<typeof vi.fn>;
const syncTable = connectorService.syncTable as ReturnType<typeof vi.fn>;
const registry = connectorService.registry as ReturnType<typeof vi.fn>;
const registerSource = connectorService.registerSource as ReturnType<typeof vi.fn>;
const testConnection = connectorService.testConnection as ReturnType<typeof vi.fn>;

// Shaped like GET /connectors/registry. postgresql lists `port` TWICE exactly as
// the real registry does (the shared base field with no default, then the
// connector's own with default 5432); bigquery/faiss carry connector-specific
// fields that travel under `extra` (BUG-170).
const field = (key: string, label: string, type: string, extra: Record<string, unknown> = {}) =>
  ({ key, label, type, required: false, ...extra });
const relationalFields = [
  field('host', 'Host', 'string', { required: true }),
  field('port', 'Port', 'number', { required: true }),
  field('database', 'Database', 'string', { required: true }),
  field('username', 'Username', 'string', { required: true }),
  field('password', 'Password', 'secret'),
  field('ssl', 'Use SSL', 'boolean'),
  field('port', 'Port', 'number', { required: true, default: 5432 }),
];
const specs = [
  { id: 'postgresql', name: 'PostgreSQL', kind: 'relational', available: true, fields: relationalFields },
  { id: 'mysql', name: 'MySQL', kind: 'relational', available: true, fields: relationalFields },
  { id: 'duckdb', name: 'DuckDB', kind: 'embedded', available: true, fields: [field('database', 'Database file', 'string', { required: true })] },
  { id: 'bigquery', name: 'BigQuery', kind: 'warehouse', available: true, fields: [field('project_id', 'Project', 'string', { required: true }), field('credentials_json', 'Credentials', 'textarea')] },
  { id: 'faiss', name: 'FAISS', kind: 'embedded', available: true, fields: [field('database', 'Path', 'string'), field('dimension', 'Dimension', 'number')] },
];

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

  // BUG-157: the empty state said "Add PostgreSQL, MySQL, or BigQuery" but no UI
  // could add a connection. The form is generated off the registry and offers
  // only connectors POST /connections can actually persist.
  describe('Add connection', () => {
    const openForm = async (user: ReturnType<typeof userEvent.setup>) => {
      await waitFor(() => expect(listSources).toHaveBeenCalled());
      await user.click(screen.getByTestId('wb-connectors-add'));
      await screen.findByTestId('wb-add-connection-form');
    };
    const fillRelational = async (user: ReturnType<typeof userEvent.setup>) => {
      await user.type(screen.getByLabelText('Name'), 'Prod warehouse');
      await user.type(screen.getByTestId('wb-conn-field-host'), 'db.example.com');
      await user.type(screen.getByTestId('wb-conn-field-database'), 'analytics');
      await user.type(screen.getByTestId('wb-conn-field-username'), 'reader');
      await user.type(screen.getByTestId('wb-conn-field-password'), 's3cret');
    };

    beforeEach(() => {
      listSources.mockResolvedValue({ connections: [], count: 0, file_sources: 0 });
      registry.mockResolvedValue(specs);
    });

    it('gives the empty state a real way to act, and no longer promises BigQuery', async () => {
      render(<ConnectorsPanel />);
      await waitFor(() => expect(screen.getByText('No connections yet')).toBeInTheDocument());
      expect(screen.getByTestId('wb-connectors-add')).toBeInTheDocument();
      expect(screen.getByText(/use add connection/i)).toBeInTheDocument();
      expect(screen.queryByText(/bigquery/i)).not.toBeInTheDocument();
    });

    it('offers every available connector, and lists a duplicated registry field once', async () => {
      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await openForm(user);

      const options = Array.from(screen.getByLabelText('Type').querySelectorAll('option')).map((o) => o.textContent);
      expect(options).toEqual(['PostgreSQL', 'MySQL', 'DuckDB', 'BigQuery', 'FAISS']);
      expect(screen.getAllByTestId('wb-conn-field-port')).toHaveLength(1);
      expect(screen.getByTestId('wb-conn-field-port')).toHaveValue(5432);
      expect(screen.getByTestId('wb-conn-field-password')).toHaveAttribute('type', 'password');
    });

    // BUG-170: connector-specific fields are sent under `extra`, not dropped.
    it('sends BigQuery settings under extra and renders credentials as a textarea', async () => {
      registerSource.mockResolvedValue({ id: 'bq-1', name: 'Warehouse', type: 'bigquery', is_active: false });
      testConnection.mockResolvedValue({ success: true, message: 'Connected. Found 3 tables.' });
      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await openForm(user);
      await user.selectOptions(screen.getByLabelText('Type'), 'bigquery');
      expect(screen.getByTestId('wb-conn-field-credentials_json').tagName).toBe('TEXTAREA');

      await user.type(screen.getByLabelText('Name'), 'Warehouse');
      await user.type(screen.getByTestId('wb-conn-field-project_id'), 'my-proj');
      await user.click(screen.getByTestId('wb-conn-field-credentials_json'));
      await user.paste('{"type":"service_account"}');
      await user.click(screen.getByTestId('wb-add-connection-save'));

      await waitFor(() => expect(registerSource).toHaveBeenCalledWith({
        name: 'Warehouse', type: 'bigquery', ssl: false,
        extra: { project_id: 'my-proj', credentials_json: '{"type":"service_account"}' },
      }));
      await waitFor(() => expect(screen.getByTestId('wb-conn-field-credentials_json')).toHaveValue(''));
    });

    it('keeps Save disabled until the name and required fields are filled', async () => {
      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await openForm(user);
      expect(screen.getByTestId('wb-add-connection-save')).toBeDisabled();
      await fillRelational(user);
      expect(screen.getByTestId('wb-add-connection-save')).toBeEnabled();
    });

    it('saves, tests the new connection, reports the result, clears the password, and refreshes the list', async () => {
      registerSource.mockResolvedValue({ id: 'new-1', name: 'Prod warehouse', type: 'postgresql', is_active: false });
      testConnection.mockResolvedValue({ success: true, message: 'Connected. Found 12 tables.' });
      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await openForm(user);
      await fillRelational(user);
      await user.click(screen.getByTestId('wb-add-connection-save'));

      await waitFor(() => expect(registerSource).toHaveBeenCalledWith({
        name: 'Prod warehouse', type: 'postgresql', host: 'db.example.com', port: 5432,
        database: 'analytics', username: 'reader', password: 's3cret', ssl: false,
      }));
      await waitFor(() => expect(testConnection).toHaveBeenCalledWith('new-1'));
      expect(await screen.findByTestId('wb-add-connection-outcome')).toHaveTextContent(/saved "prod warehouse"\. connected/i);
      expect(listSources).toHaveBeenCalledTimes(2); // initial load + refresh after create
      expect(screen.getByTestId('wb-conn-field-password')).toHaveValue('');
    });

    it('says so when the connection saved but the test failed, instead of implying it works', async () => {
      registerSource.mockResolvedValue({ id: 'new-1', name: 'Prod warehouse', type: 'postgresql', is_active: false });
      testConnection.mockResolvedValue({ success: false, message: 'password authentication failed' });
      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await openForm(user);
      await fillRelational(user);
      await user.click(screen.getByTestId('wb-add-connection-save'));

      const outcome = await screen.findByTestId('wb-add-connection-outcome');
      expect(outcome).toHaveTextContent(/saved "prod warehouse"\..*test failed.*password authentication failed/i);
      expect(outcome).toHaveAttribute('role', 'alert');
    });

    it('shows a save failure as an alert and never runs a test', async () => {
      registerSource.mockRejectedValue(new Error("Connector 'postgresql' is registered but unavailable."));
      const user = userEvent.setup();
      render(<ConnectorsPanel />);
      await openForm(user);
      await fillRelational(user);
      await user.click(screen.getByTestId('wb-add-connection-save'));

      const alert = await screen.findByTestId('wb-add-connection-error');
      expect(alert).toHaveAttribute('role', 'alert');
      expect(alert).toHaveTextContent(/unavailable/i);
      expect(testConnection).not.toHaveBeenCalled();
    });

    it('does not fetch the registry until the form is opened', async () => {
      render(<ConnectorsPanel />);
      await waitFor(() => expect(screen.getByText('No connections yet')).toBeInTheDocument());
      expect(registry).not.toHaveBeenCalled();
    });
  });
});
