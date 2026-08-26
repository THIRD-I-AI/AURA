/* Query History — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md):
   ui-kit primitives + token utilities, no inline styles. Lists real executed
   queries from GET /query-history via analyticsService. Rendered as the
   ui-kit DataTable (sortable TIME/STATUS/ROWS, filterable) — reference
   implementation for the DataTable primitive. */
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui-kit/sheet';
import { cn } from '@/lib/cn';
import { analyticsService } from '../../services/api';

type QueryRow = {
  prompt?: string; sql?: string; status?: string;
  row_count?: number | null; execution_time_ms?: number | null; timestamp?: string;
};

function statusTone(s?: string): { dot: string; text: string } {
  if (s === 'success') return { dot: 'bg-signal', text: 'text-signal' };
  if (s === 'error' || s === 'failed') return { dot: 'bg-danger', text: 'text-danger' };
  return { dot: 'bg-warn', text: 'text-warn' };
}

function fmtTime(ts?: string): string {
  if (!ts) return '—';
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

// A column-table row has no space left for the old full SQL preview
// sub-row; the QUERY cell's `title` tooltip (via `truncate: true`) carries
// the full prompt/sql text instead — an accepted trade for the density win.
const columns: ColumnDef<QueryRow>[] = [
  {
    key: 'time',
    header: 'Time',
    accessor: (q) => <span className="text-text-secondary">{fmtTime(q.timestamp)}</span>,
    sortable: true,
    sortValue: (q) => q.timestamp ?? '',
    className: 'w-44',
  },
  {
    key: 'query',
    header: 'Query',
    accessor: (q) => q.prompt || q.sql || '(query)',
    filterValue: (q) => `${q.prompt ?? ''} ${q.sql ?? ''}`,
    truncate: true,
  },
  {
    key: 'status',
    header: 'Status',
    accessor: (q) => {
      const tone = statusTone(q.status);
      return (
        <span className="inline-flex items-center gap-2">
          <span className={cn('size-1.5 shrink-0', tone.dot)} />
          <span className={cn('font-mono text-2xs font-bold tracking-wider', tone.text)}>
            {(q.status || 'unknown').toUpperCase()}
          </span>
        </span>
      );
    },
    sortable: true,
    sortValue: (q) => q.status ?? '',
    filterValue: (q) => q.status ?? '',
    className: 'w-32',
  },
  {
    key: 'rows',
    header: 'Rows',
    accessor: (q) => (typeof q.row_count === 'number' ? q.row_count : '—'),
    sortable: true,
    sortValue: (q) => q.row_count ?? -1,
    align: 'right',
    className: 'w-20',
  },
];

function DetailField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-2xs font-semibold uppercase tracking-wider text-text-tertiary">
        {label}
      </span>
      {children}
    </div>
  );
}

export default function QueryHistoryPanel() {
  const [rows, setRows] = useState<QueryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<QueryRow | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await analyticsService.getQueryHistory(50);
      setRows((resp.queries ?? []) as QueryRow[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to load query history.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const count = rows?.length ?? 0;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-queries-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {rows === null ? 'loading…' : `${count} quer${count === 1 ? 'y' : 'ies'} · this workspace`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && rows !== null && (
        <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>
      )}

      <DataTable
        columns={columns}
        rows={rows}
        error={error}
        onRetry={load}
        errorTitle="Unavailable"
        emptyTitle="No queries yet"
        emptyDescription="Ask a question in Ask AURA — it lands here with its generated SQL and status."
        filterPlaceholder="Filter queries…"
        getRowKey={(q, i) => `${q.timestamp ?? i}-${i}`}
        onRowClick={setSelected}
      />

      <Sheet open={selected !== null} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <SheetContent>
          {selected && (() => {
            const tone = statusTone(selected.status);
            return (
              <>
                <SheetHeader>
                  <SheetTitle>Query detail</SheetTitle>
                  <SheetDescription>{fmtTime(selected.timestamp)}</SheetDescription>
                </SheetHeader>
                <SheetBody className="flex flex-col gap-4">
                  <div className="flex gap-6">
                    <DetailField label="Status">
                      <span className="inline-flex items-center gap-2">
                        <span className={cn('size-1.5 shrink-0', tone.dot)} />
                        <span className={cn('font-mono text-xs font-bold tracking-wider', tone.text)}>
                          {(selected.status || 'unknown').toUpperCase()}
                        </span>
                      </span>
                    </DetailField>
                    <DetailField label="Rows">
                      <span className="font-mono text-xs text-text-primary">
                        {typeof selected.row_count === 'number' ? selected.row_count : '—'}
                      </span>
                    </DetailField>
                    <DetailField label="Execution time">
                      <span className="font-mono text-xs text-text-primary">
                        {typeof selected.execution_time_ms === 'number' ? `${selected.execution_time_ms} ms` : '—'}
                      </span>
                    </DetailField>
                  </div>
                  <DetailField label="Prompt">
                    <p className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-text-primary">
                      {selected.prompt || '—'}
                    </p>
                  </DetailField>
                  <DetailField label="SQL">
                    <pre className="whitespace-pre-wrap break-words border border-border-hairline bg-secondary p-3 font-mono text-xs leading-relaxed text-text-primary">
                      {selected.sql || '—'}
                    </pre>
                  </DetailField>
                </SheetBody>
              </>
            );
          })()}
        </SheetContent>
      </Sheet>
    </div>
  );
}
