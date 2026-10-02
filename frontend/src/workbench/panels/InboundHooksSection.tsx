/* Inbound hooks — the URLs an outside system POSTs to in order to start a
   pipeline or an agent here. BUG-257: the /hooks API (list / create / update /
   delete / fire) had no UI at all. Rendered inside the Webhooks panel, under the
   outbound webhooks. ui-kit + Tailwind token utilities (frontend/CLAUDE.md). */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { Plus, RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
import { cn } from '@/lib/cn';
import { inboundHookService, pipelineService, type InboundHook } from '../../services/api';

const FIELD = cn(
  'w-full rounded-none border border-border bg-secondary px-3 py-2 font-mono text-xs text-card-foreground',
  'placeholder:text-text-tertiary outline-none focus-visible:border-ring',
);
const LABEL = 'font-mono text-2xs text-text-tertiary';
// What the gateway accepts as a slug becomes part of a public URL; keep it URL-safe.
const SLUG = /^[a-z0-9][a-z0-9-_]{0,127}$/;

type PipelineOption = { id: string; name: string };
type RowActions = {
  busy: string | null;
  confirmDelete: string | null;
  copied: string | null;
  copyUrl: (h: InboundHook) => void;
  toggleActive: (h: InboundHook) => void;
  askDelete: (id: string | null) => void;
  remove: (h: InboundHook) => void;
};

const buildColumns = ({ busy, confirmDelete, copied, copyUrl, toggleActive, askDelete, remove }: RowActions): ColumnDef<InboundHook>[] => [
  {
    key: 'status',
    header: 'Status',
    accessor: (h) => (
      <span className={cn('font-mono text-2xs font-bold tracking-wider', h.active ? 'text-signal' : 'text-text-tertiary')}>
        {h.active ? 'ACTIVE' : 'PAUSED'}
      </span>
    ),
    sortable: true,
    sortValue: (h) => (h.active ? 1 : 0),
    className: 'w-24',
  },
  {
    key: 'slug',
    header: 'Hook',
    accessor: (h) => (
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate font-mono text-card-foreground" title={h.slug}>{h.slug}</span>
        {h.description && <span className="truncate text-xs text-text-tertiary" title={h.description}>{h.description}</span>}
      </span>
    ),
    sortable: true,
    sortValue: (h) => h.slug,
    filterValue: (h) => `${h.slug} ${h.description}`,
  },
  {
    key: 'target',
    header: 'Starts',
    accessor: (h) => `${h.kind} · ${h.target}`,
    truncate: true,
    filterValue: (h) => `${h.kind} ${h.target}`,
  },
  {
    key: 'fires',
    header: 'Fired',
    accessor: (h) => (
      <span title={h.last_fired_at ? `last ${new Date(h.last_fired_at).toLocaleString()}` : 'never fired'}>{h.fire_count}</span>
    ),
    sortable: true,
    sortValue: (h) => h.fire_count,
    align: 'right',
    className: 'w-20',
  },
  {
    key: 'signed',
    header: 'Signature',
    accessor: (h) => (
      <span className={cn('font-mono text-2xs', h.has_secret ? 'text-signal' : 'text-warn')}>
        {h.has_secret ? 'required' : 'none — open URL'}
      </span>
    ),
    className: 'w-32',
  },
  {
    key: 'actions',
    header: '',
    align: 'right',
    className: 'w-64',
    accessor: (h) => confirmDelete === h.id ? (
      <span className="flex justify-end gap-1.5">
        <Button size="xs" variant="destructive" disabled={busy === h.id} onClick={() => remove(h)}>Confirm delete</Button>
        <Button size="xs" variant="ghost" onClick={() => askDelete(null)}>Cancel</Button>
      </span>
    ) : (
      <span className="flex justify-end gap-1.5">
        <Button size="xs" variant="outline" aria-label={`Copy URL for ${h.slug}`} onClick={() => copyUrl(h)}>
          {copied === h.id ? 'Copied' : 'Copy URL'}
        </Button>
        <Button size="xs" variant="outline" disabled={busy === h.id} aria-label={`${h.active ? 'Pause' : 'Resume'} ${h.slug}`} onClick={() => toggleActive(h)}>
          {h.active ? 'Pause' : 'Resume'}
        </Button>
        <Button size="xs" variant="ghost" className="text-danger" disabled={busy === h.id} aria-label={`Delete ${h.slug}`} onClick={() => askDelete(h.id)}>Delete</Button>
      </span>
    ),
  },
];

export default function InboundHooksSection() {
  const [hooks, setHooks] = useState<InboundHook[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setHooks((await inboundHookService.list()).hooks ?? []);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list inbound hooks.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const copyUrl = useCallback(async (h: InboundHook) => {
    const url = inboundHookService.fireUrl(h.slug);
    try {
      await navigator.clipboard.writeText(url);
      setCopied(h.id);
      setNotice(null);
    } catch {
      // Clipboard access can be refused; show the URL so it can be copied by hand.
      setNotice(`POST to ${url}`);
    }
  }, []);

  const toggleActive = useCallback(async (h: InboundHook) => {
    setBusy(h.id);
    setNotice(null);
    try {
      await inboundHookService.update(h.id, { active: !h.active });
      await load();
    } catch {
      setNotice(`Could not ${h.active ? 'pause' : 'resume'} ${h.slug}.`);
    } finally {
      setBusy(null);
    }
  }, [load]);

  const remove = useCallback(async (h: InboundHook) => {
    setBusy(h.id);
    setConfirmDelete(null);
    setNotice(null);
    try {
      await inboundHookService.remove(h.id);
      await load();
    } catch {
      setNotice(`Could not delete ${h.slug}.`);
    } finally {
      setBusy(null);
    }
  }, [load]);

  const columns = useMemo(
    () => buildColumns({ busy, confirmDelete, copied, copyUrl, toggleActive, askDelete: setConfirmDelete, remove }),
    [busy, confirmDelete, copied, copyUrl, toggleActive, remove],
  );

  // ── create ──
  const [creating, setCreating] = useState(false);
  const [slug, setSlug] = useState('');
  const [kind, setKind] = useState<'pipeline' | 'agent'>('pipeline');
  const [target, setTarget] = useState('');
  const [secret, setSecret] = useState('');
  const [description, setDescription] = useState('');
  const [pipelines, setPipelines] = useState<PipelineOption[] | null>(null);
  const [pipelinesError, setPipelinesError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const openCreate = useCallback(async () => {
    setCreating(true);
    setFormError(null);
    setPipelinesError(false);
    try {
      setPipelines(((await pipelineService.list()).pipelines ?? []).map((p) => ({ id: p.id, name: p.name })));
    } catch {
      setPipelines([]);
      setPipelinesError(true);
    }
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!SLUG.test(slug.trim())) {
      setFormError('The slug becomes part of the URL: lowercase letters, digits, "-" and "_" only.');
      return;
    }
    if (!target.trim()) {
      setFormError(kind === 'pipeline' ? 'Pick the pipeline this hook starts.' : 'Describe what the agent should do when the hook fires.');
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      await inboundHookService.create({
        slug: slug.trim(), kind, target: target.trim(),
        secret: secret || undefined, description: description.trim() || undefined,
      });
      setSlug(''); setTarget(''); setSecret(''); setDescription(''); setCreating(false);
      await load();
    } catch (err) {
      setFormError(err instanceof Error && err.message ? err.message : 'Could not create the hook.');
    } finally {
      setSaving(false);
    }
  };

  const count = hooks?.length ?? 0;

  return (
    <section aria-label="Inbound hooks" className="flex flex-col gap-3.5" data-testid="wb-inbound-hooks">
      <div className="flex items-center gap-3">
        <h3 className="font-display text-sm font-semibold text-card-foreground">Inbound hooks</h3>
        <span className="font-mono text-2xs text-text-tertiary">
          {hooks === null ? (error ? 'unavailable' : 'loading…') : `${count} hook${count === 1 ? '' : 's'} · URLs that start a pipeline or agent`}
        </span>
        <div className="flex-1" />
        <Button size="sm" onClick={() => (creating ? setCreating(false) : openCreate())} aria-expanded={creating}>
          <Plus /> New hook
        </Button>
        <Button variant="outline" size="sm" onClick={load} aria-label="Refresh inbound hooks">
          <RefreshCw /> Refresh
        </Button>
      </div>

      {creating && (
        <form onSubmit={submit} className="flex flex-col gap-2 border border-border bg-card p-3" data-testid="wb-inbound-hook-new">
          <label htmlFor="wb-hook-slug" className={LABEL}>Slug</label>
          <input id="wb-hook-slug" name="slug" value={slug} maxLength={128} onChange={(e) => setSlug(e.target.value)}
            placeholder="orders-arrived" aria-invalid={!!formError && !SLUG.test(slug.trim())} className={FIELD} />
          {slug.trim() && SLUG.test(slug.trim()) && (
            <span className="truncate font-mono text-2xs text-text-tertiary" title={inboundHookService.fireUrl(slug.trim())}>
              POST {inboundHookService.fireUrl(slug.trim())}
            </span>
          )}

          <label htmlFor="wb-hook-kind" className={LABEL}>Starts</label>
          <select id="wb-hook-kind" name="kind" value={kind} className={cn(FIELD, 'cursor-pointer')}
            onChange={(e) => { setKind(e.target.value as 'pipeline' | 'agent'); setTarget(''); }}>
            <option value="pipeline">A saved pipeline</option>
            <option value="agent">An agent prompt</option>
          </select>

          {kind === 'pipeline' ? (
            <>
              <label htmlFor="wb-hook-target" className={LABEL}>Pipeline</label>
              <select id="wb-hook-target" name="target" value={target} onChange={(e) => setTarget(e.target.value)}
                aria-invalid={!!formError && !target} className={cn(FIELD, 'cursor-pointer')}>
                <option value="">{pipelines === null ? 'Loading pipelines…' : 'Select a pipeline…'}</option>
                {(pipelines ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              {pipelinesError && <span role="alert" className="font-mono text-2xs text-danger">Could not load your pipelines.</span>}
              {pipelines !== null && !pipelinesError && pipelines.length === 0 && (
                <span className="font-mono text-2xs text-text-tertiary">No saved pipelines yet. Save one in Pipelines first.</span>
              )}
            </>
          ) : (
            <>
              <label htmlFor="wb-hook-target" className={LABEL}>Agent prompt</label>
              <textarea id="wb-hook-target" name="target" value={target} rows={3} onChange={(e) => setTarget(e.target.value)}
                placeholder="Summarise the incoming order and flag anything unusual."
                aria-invalid={!!formError && !target.trim()} className={cn(FIELD, 'resize-y')} />
            </>
          )}

          <label htmlFor="wb-hook-secret" className={LABEL}>Signing secret (recommended)</label>
          <input id="wb-hook-secret" name="secret" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} className={FIELD} />
          <span className="font-mono text-2xs text-text-tertiary">
            With a secret, callers must sign the body (HMAC-SHA256, x-aura-signature). Without one, anyone who knows the URL can fire the hook.
          </span>

          <label htmlFor="wb-hook-description" className={LABEL}>Description (optional)</label>
          <input id="wb-hook-description" name="description" value={description} onChange={(e) => setDescription(e.target.value)} className={FIELD} />

          {formError && <div role="alert" className="font-mono text-xs text-danger">{formError}</div>}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={saving}>{saving ? 'Creating…' : 'Create hook'}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </form>
      )}

      {notice && (
        <div role="status" className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-text-secondary">{notice}</div>
      )}

      <DataTable
        columns={columns}
        rows={hooks}
        error={error}
        onRetry={load}
        errorTitle="Unavailable"
        emptyTitle="No inbound hooks"
        emptyDescription="Use New hook to get a URL that an outside system can POST to in order to start a pipeline or an agent."
        filterPlaceholder="Filter hooks…"
        getRowKey={(h) => h.id}
      />
    </section>
  );
}
