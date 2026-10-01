/* Library — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Lists real saved queries from
   GET /saved-queries via savedQueryService. BUG-257: this panel is also where a
   query is created -- nothing in the app called savedQueryService.create, so the
   Library, Scheduler and Lineage views could never be filled from the UI. */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { Plus, RefreshCw, Star, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
import { cn } from '@/lib/cn';
import { savedQueryService } from '../../services/api';

type SavedQuery = { id: string; name?: string; sql?: string; prompt?: string; starred?: boolean };

// Starred column sorts boolean-first (starred items surface at the top) —
// a real, meaningful order, unlike a rendered icon with no underlying value.
// SQL keeps the QueryHistoryPanel precedent: a `truncate` cell with its
// `title` tooltip carries the full text instead of a separate preview sub-row.
const FIELD = cn(
  'w-full rounded-none border border-border bg-secondary px-3 py-2 font-mono text-xs text-card-foreground',
  'placeholder:text-text-tertiary outline-none focus-visible:border-ring',
);

type RowActions = {
  busy: string | null;
  confirmDelete: string | null;
  toggleStar: (q: SavedQuery) => void;
  askDelete: (id: string | null) => void;
  remove: (q: SavedQuery) => void;
};

const buildColumns = ({ busy, confirmDelete, toggleStar, askDelete, remove }: RowActions): ColumnDef<SavedQuery>[] => [
  {
    key: 'starred',
    header: 'Starred',
    accessor: (q) => (
      <Button
        variant="ghost" size="icon-xs" aria-pressed={!!q.starred} disabled={busy === q.id}
        aria-label={`${q.starred ? 'Unstar' : 'Star'} ${q.name || 'query'}`}
        onClick={() => toggleStar(q)}
      >
        <Star className={cn('size-3.5 shrink-0', q.starred ? 'fill-warn text-warn' : 'text-text-tertiary')} />
      </Button>
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
  {
    key: 'actions',
    header: '',
    className: 'w-40',
    align: 'right',
    accessor: (q) => confirmDelete === q.id ? (
      <span className="flex justify-end gap-1.5">
        <Button size="xs" variant="destructive" disabled={busy === q.id} onClick={() => remove(q)}>Confirm delete</Button>
        <Button size="xs" variant="ghost" onClick={() => askDelete(null)}>Cancel</Button>
      </span>
    ) : (
      <Button
        variant="ghost" size="icon-xs" className="text-text-tertiary hover:text-danger"
        aria-label={`Delete ${q.name || 'query'}`} disabled={busy === q.id} onClick={() => askDelete(q.id)}
      >
        <Trash2 className="size-3.5" />
      </Button>
    ),
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

  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [sql, setSql] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const toggleStar = useCallback(async (q: SavedQuery) => {
    setBusy(q.id);
    try {
      await savedQueryService.update(q.id, { starred: !q.starred });
      await load();
    } catch {
      setError(`Could not update "${q.name || 'query'}".`);
    } finally {
      setBusy(null);
    }
  }, [load]);

  const remove = useCallback(async (q: SavedQuery) => {
    setBusy(q.id);
    setConfirmDelete(null);
    try {
      await savedQueryService.remove(q.id);
      await load();
    } catch {
      setError(`Could not delete "${q.name || 'query'}".`);
    } finally {
      setBusy(null);
    }
  }, [load]);

  const columns = useMemo(
    () => buildColumns({ busy, confirmDelete, toggleStar, askDelete: setConfirmDelete, remove }),
    [busy, confirmDelete, toggleStar, remove],
  );

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !sql.trim()) {
      setFormError('A name and a SQL statement are both required.');
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      await savedQueryService.create({ name: name.trim(), sql: sql.trim() });
      setName('');
      setSql('');
      setCreating(false);
      await load();
    } catch (err) {
      setFormError(err instanceof Error && err.message ? err.message : 'Could not save the query.');
    } finally {
      setSaving(false);
    }
  };

  const count = items?.length ?? 0;
  const starred = (items ?? []).filter((q) => q.starred).length;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-library-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {items === null ? 'loading…' : `${count} saved quer${count === 1 ? 'y' : 'ies'}${starred ? ` · ${starred} starred` : ''}`}
        </span>
        <div className="flex-1" />
        <Button size="sm" onClick={() => { setCreating((v) => !v); setFormError(null); }} aria-expanded={creating}>
          <Plus /> New query
        </Button>
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {creating && (
        <form onSubmit={submit} className="flex flex-col gap-2 border border-border bg-card p-3" data-testid="wb-library-new">
          <label htmlFor="wb-library-name" className="font-mono text-2xs text-text-tertiary">Name</label>
          <input
            id="wb-library-name" name="name" value={name} maxLength={200}
            onChange={(e) => setName(e.target.value)} placeholder="Monthly revenue by region"
            aria-invalid={!!formError && !name.trim()} className={FIELD}
          />
          <label htmlFor="wb-library-sql" className="font-mono text-2xs text-text-tertiary">SQL</label>
          <textarea
            id="wb-library-sql" name="sql" value={sql} rows={5} spellCheck={false}
            onChange={(e) => setSql(e.target.value)} placeholder="SELECT region, SUM(amount) FROM sales GROUP BY region"
            aria-invalid={!!formError && !sql.trim()} className={cn(FIELD, 'resize-y')}
          />
          {formError && <div role="alert" className="font-mono text-xs text-danger">{formError}</div>}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={saving}>{saving ? 'Saving…' : 'Save query'}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </form>
      )}

      {error && items !== null && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <DataTable
        columns={columns}
        rows={items}
        error={error}
        onRetry={load}
        errorTitle="Unavailable"
        emptyTitle="No saved queries yet"
        emptyDescription="Use New query to save a SQL statement. Saved queries can be scheduled and placed on dashboards."
        filterPlaceholder="Filter library…"
        getRowKey={(q) => q.id}
      />
    </div>
  );
}
