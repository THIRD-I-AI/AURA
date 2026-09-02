/* Connectors — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real data sources from
   GET /connections via connectorService: database connections + file sources.

   "Sync to Chat" row action materializes a connection's table as a queryable
   snapshot (POST /connections/:id/sync) so Ask AURA can query it — mirrors
   SchedulerPanel's inline-expand-in-place pattern (frontend.md: "row actions
   live inline ... not a modal, for anything already visible in a dense
   table") rather than opening a Sheet/dialog. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, DatabaseZap } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
import { cn } from '@/lib/cn';
import { connectorService } from '../../services/api';

type Connection = { id?: string; name?: string; type?: string; source_id?: string; status?: string };
type SourcesResp = { connections?: Connection[]; count?: number; file_sources?: number };

function rowKey(c: Connection, i: number): string {
  return c.id || c.source_id || String(i);
}

// DataTable's ColumnDef#accessor only receives `row`, not its post-filter/sort
// index, so the sync picker can't reuse rowKey's index fallback — it keys off
// fields stable across renders instead. Every connection from the registry
// carries a real `id`, so the fallback chain only matters for a malformed row.
function syncKey(c: Connection): string {
  return c.id || c.source_id || c.name || 'connection';
}

interface SyncCellProps {
  connection: Connection;
  id: string;
  open: boolean;
  busy: boolean;
  result: { tableName: string; rowCount: number } | null;
  tables: string[] | null;
  schemaError: string | null;
  selectedTable: string;
  syncError: string | null;
  onOpen: () => void;
  onTableChange: (table: string) => void;
  onSync: () => void;
  onClose: () => void;
}

/* Hoisted to module scope (not defined inside ConnectorsPanel's render) so
   React treats it as a stable component type across renders — an inline
   nested component would remount on every keystroke elsewhere in the panel. */
function SyncCell({
  connection: c, id, open, busy, result, tables, schemaError, selectedTable, syncError,
  onOpen, onTableChange, onSync, onClose,
}: SyncCellProps) {
  if (result) {
    return (
      <div className="flex flex-col gap-1" data-testid={`wb-connector-sync-result-${id}`}>
        <span className="font-mono text-2xs font-semibold text-signal">
          Synced {result.rowCount.toLocaleString()} row{result.rowCount === 1 ? '' : 's'} from "{result.tableName}"
        </span>
        <span className="text-2xs text-text-tertiary">Ask AURA can now query this table.</span>
      </div>
    );
  }

  if (!open) {
    return (
      <Button variant="outline" size="xs" onClick={onOpen} data-testid={`wb-connector-sync-open-${id}`}>
        <DatabaseZap /> Sync to chat
      </Button>
    );
  }

  const loadingTables = tables === null && !schemaError;

  return (
    <div className="flex flex-col gap-1.5 py-1" data-testid={`wb-connector-sync-picker-${id}`}>
      <label htmlFor={`wb-connector-sync-table-${id}`} className="sr-only">
        Table to sync from {c.name || c.source_id || 'this connection'}
      </label>
      {loadingTables && <span className="font-mono text-2xs text-text-tertiary">Loading tables…</span>}
      {schemaError && <span className="font-mono text-2xs text-danger">{schemaError}</span>}
      {tables !== null && tables.length === 0 && !schemaError && (
        <span className="font-mono text-2xs text-text-tertiary">No tables found on this connection.</span>
      )}
      {tables !== null && tables.length > 0 && (
        <div className="flex items-center gap-1.5">
          <select
            id={`wb-connector-sync-table-${id}`}
            value={selectedTable}
            onChange={(e) => onTableChange(e.target.value)}
            disabled={busy}
            className={cn(
              'h-6 min-w-0 flex-1 rounded-none border border-border-hairline bg-secondary px-1.5 font-mono text-2xs text-card-foreground',
              'outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
              'disabled:pointer-events-none disabled:opacity-50',
            )}
          >
            {tables.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <Button size="xs" onClick={onSync} disabled={busy || !selectedTable}>
            {busy ? 'Syncing…' : 'Sync'}
          </Button>
        </div>
      )}
      {syncError && <span className="font-mono text-2xs text-danger">{syncError}</span>}
      <Button variant="ghost" size="xs" onClick={onClose} disabled={busy} className="self-start">
        Cancel
      </Button>
    </div>
  );
}

export default function ConnectorsPanel() {
  const [data, setData] = useState<SourcesResp | null>(null);
  const [error, setError] = useState<string | null>(null);

  // "Sync to Chat" picker state, keyed by row id — only one row's picker is
  // open at a time, everything else here is honest fetched/derived state.
  const [pickerRowId, setPickerRowId] = useState<string | null>(null);
  const [tables, setTables] = useState<string[] | null>(null);
  const [selectedTable, setSelectedTable] = useState('');
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [syncBusyId, setSyncBusyId] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<{ rowId: string; tableName: string; rowCount: number } | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = (await connectorService.listSources()) as SourcesResp;
      setData(resp);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list connectors.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const closePicker = useCallback(() => {
    setPickerRowId(null);
    setTables(null);
    setSelectedTable('');
    setSchemaError(null);
    setSyncError(null);
  }, []);

  const openPicker = useCallback(async (c: Connection, id: string) => {
    if (pickerRowId === id) { closePicker(); return; }
    setPickerRowId(id);
    setTables(null);
    setSelectedTable('');
    setSchemaError(null);
    setSyncError(null);
    setSyncResult(null);
    try {
      const schema = await connectorService.getSchema(id);
      const names = Object.keys(schema);
      setTables(names);
      setSelectedTable(names[0] ?? '');
    } catch {
      setSchemaError(`Could not load tables for "${c.name || c.source_id || 'this connection'}".`);
      setTables([]);
    }
  }, [pickerRowId, closePicker]);

  const runSync = useCallback(async (c: Connection, id: string) => {
    if (!selectedTable) return;
    setSyncBusyId(id);
    setSyncError(null);
    try {
      const result = await connectorService.syncTable(id, selectedTable);
      setSyncResult({ rowId: id, tableName: result.table_name, rowCount: result.row_count });
      setPickerRowId(null);
      setTables(null);
      setSelectedTable('');
    } catch {
      setSyncError(`Could not sync "${selectedTable}" from "${c.name || c.source_id || 'this connection'}".`);
    } finally {
      setSyncBusyId(null);
    }
  }, [selectedTable]);

  const columns = useMemo<ColumnDef<Connection>[]>(() => [
    {
      key: 'name',
      header: 'Name',
      accessor: (c) => <span className="text-card-foreground">{c.name || c.source_id || '(source)'}</span>,
      sortable: true,
      sortValue: (c) => c.name || c.source_id || '',
    },
    {
      key: 'type',
      header: 'Type',
      accessor: (c) => <span className="font-mono text-2xs font-semibold tracking-wider text-text-tertiary">{(c.type || 'db').toUpperCase()}</span>,
      sortable: true,
      sortValue: (c) => c.type ?? '',
      className: 'w-28',
    },
    {
      key: 'status',
      header: 'Status',
      accessor: (c) => (
        <span className="inline-flex items-center gap-2">
          <span className={cn('size-1.5 shrink-0', c.status === 'connected' ? 'bg-signal' : 'bg-warn')} />
          <span className={cn('font-mono text-2xs font-bold tracking-wider', c.status === 'connected' ? 'text-signal' : 'text-warn')}>
            {(c.status || 'unknown').toUpperCase()}
          </span>
        </span>
      ),
      sortable: true,
      sortValue: (c) => c.status ?? '',
      className: 'w-32',
    },
    {
      key: 'sync',
      header: 'Sync to chat',
      className: 'w-72',
      accessor: (c) => {
        const id = syncKey(c);
        return (
          <SyncCell
            connection={c}
            id={id}
            open={pickerRowId === id}
            busy={syncBusyId === id}
            result={syncResult?.rowId === id ? syncResult : null}
            tables={pickerRowId === id ? tables : null}
            schemaError={pickerRowId === id ? schemaError : null}
            selectedTable={selectedTable}
            syncError={syncBusyId === id || pickerRowId === id ? syncError : null}
            onOpen={() => openPicker(c, id)}
            onTableChange={setSelectedTable}
            onSync={() => runSync(c, id)}
            onClose={closePicker}
          />
        );
      },
    },
  ], [pickerRowId, syncBusyId, syncResult, tables, schemaError, selectedTable, syncError, openPicker, runSync, closePicker]);

  const conns = data?.connections ?? [];
  const fileSources = data?.file_sources ?? 0;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-connectors-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {data === null && !error ? 'loading…' : `${conns.length} database connection${conns.length === 1 ? '' : 's'} · ${fileSources} file source${fileSources === 1 ? '' : 's'}`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && data !== null && (
        <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>
      )}

      <div className="font-mono text-2xs font-semibold uppercase tracking-widest text-text-tertiary">Database connections</div>

      <DataTable
        columns={columns}
        rows={data === null ? null : conns}
        error={error}
        onRetry={load}
        errorTitle="Unavailable"
        emptyTitle="No connections yet"
        emptyDescription="Add PostgreSQL, MySQL, or BigQuery to query live warehouses alongside your files."
        filterPlaceholder="Filter connections…"
        getRowKey={(c, i) => rowKey(c, i)}
      />

      <Panel>
        <div className="flex items-center gap-2 px-4 py-2.5">
          <div className="font-mono text-2xs font-semibold uppercase tracking-widest text-text-tertiary">File sources</div>
          <div className="flex-1" />
          <span className="font-mono text-2xs text-signal">{fileSources} active</span>
        </div>
        <div className="px-4 pb-3.5 text-xs leading-relaxed text-text-tertiary">
          Uploaded datasets are auto-registered as queryable sources — manage them in <span className="text-text-secondary">Files &amp; Data</span>.
        </div>
      </Panel>
    </div>
  );
}
