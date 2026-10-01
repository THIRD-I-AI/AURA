/* Dashboards — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real saved dashboards from
   GET /dashboards via dashboardService. BUG-257: the panel was list-only --
   nothing could create a dashboard, open one, or remove one, so the create /
   render / delete endpoints had no way in from the app. */
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Plus, RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { dashboardService, savedQueryService, type DashboardRender, type RenderedTile } from '../../services/api';

type Tile = { id?: string };
type Dashboard = { id: string; name?: string; description?: string | null; tiles?: Tile[]; updated_at?: string };
type QueryOption = { id: string; name?: string };

// Mirrors the gateway's MAX_DASHBOARD_TILES (BUG-234): more is a 422.
const MAX_TILES = 50;
// Rows shown per tile here; the gateway already caps what it returns.
const TILE_ROWS_SHOWN = 20;

const FIELD = cn(
  'w-full rounded-none border border-border bg-secondary px-3 py-2 font-mono text-xs text-card-foreground',
  'placeholder:text-text-tertiary outline-none focus-visible:border-ring',
);

function TileResult({ tile }: { tile: RenderedTile }) {
  const rows = tile.rows.slice(0, TILE_ROWS_SHOWN);
  const cut = tile.truncated || tile.rows.length > rows.length;
  return (
    <div className="flex min-w-0 flex-col gap-1.5 border border-border bg-card p-3" data-testid={`dashboard-render-tile-${tile.tile_id}`}>
      <div className="truncate text-xs font-semibold text-card-foreground" title={tile.title ?? undefined}>{tile.title || tile.saved_query_id}</div>
      {tile.status === 'missing' && <div className="font-mono text-2xs text-warn">The saved query behind this tile no longer exists.</div>}
      {tile.status === 'error' && <div className="font-mono text-2xs text-danger">{tile.error || 'This tile failed to run.'}</div>}
      {tile.status === 'success' && tile.rows.length === 0 && <div className="font-mono text-2xs text-text-tertiary">No rows.</div>}
      {tile.status === 'success' && tile.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-2xs">
            <thead>
              <tr>{tile.columns.map((c) => <th key={c} scope="col" className="border-b border-border px-2 py-1 text-left font-semibold text-text-secondary">{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{r.map((v, j) => <td key={j} className="max-w-48 truncate border-b border-border-hairline px-2 py-1 text-card-foreground" title={String(v ?? '')}>{v === null || v === undefined ? '—' : String(v)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {tile.status === 'success' && cut && (
        <div className="font-mono text-2xs text-text-tertiary">Showing the first {rows.length} rows.</div>
      )}
    </div>
  );
}

export default function DashboardsPanel() {
  const [items, setItems] = useState<Dashboard[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await dashboardService.list();
      setItems((list ?? []) as Dashboard[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to load dashboards.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // ── create ──
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [queries, setQueries] = useState<QueryOption[] | null>(null);
  const [queriesError, setQueriesError] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const openCreate = useCallback(async () => {
    setCreating(true);
    setFormError(null);
    setQueriesError(false);
    try {
      setQueries((await savedQueryService.list()) as QueryOption[]);
    } catch {
      setQueries([]);
      setQueriesError(true);
    }
  }, []);

  const togglePicked = (id: string) =>
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setFormError('A dashboard needs a name.'); return; }
    if (picked.length > MAX_TILES) { setFormError(`A dashboard can hold at most ${MAX_TILES} tiles.`); return; }
    setSaving(true);
    setFormError(null);
    try {
      const byId = new Map((queries ?? []).map((q) => [q.id, q]));
      await dashboardService.create({
        name: name.trim(),
        description: description.trim() || undefined,
        tiles: picked.map((id) => ({ saved_query_id: id, title: byId.get(id)?.name, chart_type: 'table' })),
      });
      setName(''); setDescription(''); setPicked([]); setCreating(false);
      await load();
    } catch (err) {
      setFormError(err instanceof Error && err.message ? err.message : 'Could not create the dashboard.');
    } finally {
      setSaving(false);
    }
  };

  // ── open / delete ──
  const [openId, setOpenIdState] = useState<string | null>(null);
  // Mirrors openId for the async render below: only the dashboard still open may
  // show its result (a slower earlier open must not overwrite a later one).
  const openRef = useRef<string | null>(null);
  const setOpenId = useCallback((id: string | null) => { openRef.current = id; setOpenIdState(id); }, []);
  const [rendered, setRendered] = useState<DashboardRender | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const open = useCallback(async (d: Dashboard) => {
    setOpenId(d.id);
    setRendered(null);
    setRenderError(null);
    try {
      const result = await dashboardService.render(d.id);
      if (openRef.current === d.id) setRendered(result);
    } catch {
      if (openRef.current === d.id) setRenderError(`Could not render "${d.name || 'dashboard'}".`);
    }
  }, [setOpenId]);

  const remove = useCallback(async (d: Dashboard) => {
    setBusy(d.id);
    setConfirmDelete(null);
    try {
      await dashboardService.remove(d.id);
      if (openRef.current === d.id) setOpenId(null);
      await load();
    } catch {
      setError(`Could not delete "${d.name || 'dashboard'}".`);
    } finally {
      setBusy(null);
    }
  }, [load, setOpenId]);

  const count = items?.length ?? 0;
  const opened = (items ?? []).find((d) => d.id === openId) ?? null;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-dashboards-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {items === null ? (error ? 'unavailable' : 'loading…') : `${count} dashboard${count === 1 ? '' : 's'} · this workspace`}
        </span>
        <div className="flex-1" />
        <Button size="sm" onClick={() => (creating ? setCreating(false) : openCreate())} aria-expanded={creating}>
          <Plus /> New dashboard
        </Button>
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      {creating && (
        <form onSubmit={submit} className="flex flex-col gap-2 border border-border bg-card p-3" data-testid="wb-dashboard-new">
          <label htmlFor="wb-dashboard-name" className="font-mono text-2xs text-text-tertiary">Name</label>
          <input id="wb-dashboard-name" name="name" value={name} maxLength={200} onChange={(e) => setName(e.target.value)}
            placeholder="Weekly revenue" aria-invalid={!!formError && !name.trim()} className={FIELD} />
          <label htmlFor="wb-dashboard-description" className="font-mono text-2xs text-text-tertiary">Description (optional)</label>
          <input id="wb-dashboard-description" name="description" value={description} onChange={(e) => setDescription(e.target.value)} className={FIELD} />
          <fieldset className="flex flex-col gap-1 border border-border p-2">
            <legend className="px-1 font-mono text-2xs text-text-tertiary">Tiles — one per saved query ({picked.length} selected)</legend>
            {queries === null && <span className="font-mono text-2xs text-text-tertiary">Loading saved queries…</span>}
            {queriesError && <span role="alert" className="font-mono text-2xs text-danger">Could not load saved queries.</span>}
            {queries !== null && !queriesError && queries.length === 0 && (
              <span className="font-mono text-2xs text-text-tertiary">No saved queries yet. Create one in Library, then add it here.</span>
            )}
            {(queries ?? []).map((q) => (
              <label key={q.id} className="flex items-center gap-2 text-xs text-card-foreground">
                <input type="checkbox" checked={picked.includes(q.id)} onChange={() => togglePicked(q.id)} />
                <span className="truncate" title={q.name}>{q.name || '(untitled)'}</span>
              </label>
            ))}
          </fieldset>
          {formError && <div role="alert" className="font-mono text-xs text-danger">{formError}</div>}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={saving}>{saving ? 'Creating…' : 'Create dashboard'}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </form>
      )}

      {items !== null && count === 0 && !error ? (
        <Panel>
          <EmptyState intent="empty" title="No dashboards yet" description="Use New dashboard to combine saved queries into one live view of your workspace metrics." />
        </Panel>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(min(260px,100%),1fr))] gap-3">
          {items === null && !error && <Panel className="p-4 text-xs text-text-tertiary">Loading…</Panel>}
          {error && items === null && (
            <Panel>
              <EmptyState intent="error" title="Unavailable" action={<Button variant="outline" size="sm" onClick={load}>Retry</Button>} />
            </Panel>
          )}
          {(items ?? []).map((d) => (
            <Panel key={d.id} data-testid="dashboard-tile" className="flex flex-col gap-1.5 p-4">
              <div className="flex items-center gap-2">
                <span className="size-1.5 shrink-0 bg-signal" />
                <span className="truncate text-sm font-semibold text-card-foreground" title={d.name}>{d.name || '(untitled)'}</span>
              </div>
              {d.description && <div className="text-xs leading-snug text-text-tertiary">{d.description}</div>}
              <div className="mt-0.5 font-mono text-2xs text-text-tertiary">{(d.tiles?.length ?? 0)} tile{(d.tiles?.length ?? 0) === 1 ? '' : 's'}</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {confirmDelete === d.id ? (
                  <>
                    <Button size="xs" variant="destructive" disabled={busy === d.id} onClick={() => remove(d)}>Confirm delete</Button>
                    <Button size="xs" variant="ghost" onClick={() => setConfirmDelete(null)}>Cancel</Button>
                  </>
                ) : (
                  <>
                    <Button size="xs" variant="outline" aria-label={`Open ${d.name || 'dashboard'}`} onClick={() => open(d)}>
                      {openId === d.id ? 'Refresh data' : 'Open'}
                    </Button>
                    <Button size="xs" variant="ghost" className="text-danger" aria-label={`Delete ${d.name || 'dashboard'}`} disabled={busy === d.id} onClick={() => setConfirmDelete(d.id)}>
                      Delete
                    </Button>
                  </>
                )}
              </div>
            </Panel>
          ))}
        </div>
      )}

      {opened && (
        <section aria-label={`${opened.name || 'Dashboard'} tiles`} className="flex flex-col gap-2.5" data-testid="wb-dashboard-open">
          <div className="flex items-center gap-3">
            <h3 className="font-display text-sm font-semibold text-card-foreground">{opened.name || '(untitled)'}</h3>
            {rendered && <span className="font-mono text-2xs text-text-tertiary">rendered {new Date(rendered.rendered_at).toLocaleTimeString()}</span>}
            <div className="flex-1" />
            <Button size="xs" variant="ghost" onClick={() => setOpenId(null)}>Close</Button>
          </div>
          {!rendered && !renderError && <Panel><EmptyState intent="awaiting" title="Running tiles…" /></Panel>}
          {renderError && (
            <Panel>
              <EmptyState intent="error" title="Could not render" description={renderError}
                action={<Button variant="outline" size="sm" onClick={() => open(opened)}>Retry</Button>} />
            </Panel>
          )}
          {rendered && rendered.tiles.length === 0 && (
            <Panel><EmptyState intent="empty" title="No tiles" description="This dashboard has no tiles yet." /></Panel>
          )}
          {rendered && rendered.tiles.length > 0 && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(min(340px,100%),1fr))] gap-3">
              {rendered.tiles.map((t) => <TileResult key={t.tile_id} tile={t} />)}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
