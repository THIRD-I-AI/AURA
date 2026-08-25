/* Library — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Lists real saved queries from
   GET /saved-queries via savedQueryService. Read + star; full editing stays in
   the query flow that created them. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, Star } from 'lucide-react';

import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
import { cn } from '@/lib/cn';
import { savedQueryService } from '../../services/api';

type SavedQuery = { id: string; name?: string; sql?: string; prompt?: string; starred?: boolean };

// Starred column sorts boolean-first (starred items surface at the top) —
// a real, meaningful order, unlike a rendered icon with no underlying value.
// SQL keeps the QueryHistoryPanel precedent: a `truncate` cell with its
// `title` tooltip carries the full text instead of a separate preview sub-row.
const columns: ColumnDef<SavedQuery>[] = [
  {
    key: 'starred',
    header: 'Starred',
    accessor: (q) => (
      <Star className={cn('size-3.5 shrink-0', q.starred ? 'fill-warn text-warn' : 'text-text-tertiary')} />
    ),
    sortable: true,
    sortValue: (q) => (q.starred ? 1 : 0),
    className: 'w-16',
  },
  {
    key: 'name',
    header: 'Name',
    accessor: (q) => (
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate text-sm font-semibold text-card-foreground">{q.name || '(untitled)'}</span>
        {q.prompt && q.prompt !== q.name && (
          <span className="truncate text-xs text-text-tertiary">{q.prompt}</span>
        )}
      </span>
    ),
    sortable: true,
    sortValue: (q) => q.name ?? '',
    filterValue: (q) => `${q.name ?? ''} ${q.prompt ?? ''}`,
  },
  {
    key: 'sql',
    header: 'SQL',
    accessor: (q) => q.sql || '—',
    truncate: true,
    filterValue: (q) => q.sql ?? '',
  },
];

export default function LibraryPanel() {
  const [items, setItems] = useState<SavedQuery[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await savedQueryService.list();
      setItems((list ?? []) as SavedQuery[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to load the query library.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const count = items?.length ?? 0;
  const starred = (items ?? []).filter((q) => q.starred).length;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-library-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {items === null ? 'loading…' : `${count} saved quer${count === 1 ? 'y' : 'ies'}${starred ? ` · ${starred} starred` : ''}`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && items !== null && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <DataTable
        columns={columns}
        rows={items}
        error={error}
        onRetry={load}
        errorTitle="Unavailable"
        emptyTitle="No saved queries yet"
        emptyDescription="Save a query from Ask AURA and it appears here for one-click reuse."
        filterPlaceholder="Filter library…"
        getRowKey={(q) => q.id}
      />
    </div>
  );
}
