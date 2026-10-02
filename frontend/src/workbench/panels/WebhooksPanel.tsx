/* Webhooks — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real outbound webhooks from
   GET /webhooks via webhookService. BUG-257: the panel could only list them --
   register, test, pause/resume and delete had no way in from the app. */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { Plus, RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
import { cn } from '@/lib/cn';
import { webhookService } from '../../services/api';
import InboundHooksSection from './InboundHooksSection';

type Webhook = { id: string; url: string; events: string[]; active: boolean; retries: number; description?: string };

const FIELD = cn(
  'w-full rounded-none border border-border bg-secondary px-3 py-2 font-mono text-xs text-card-foreground',
  'placeholder:text-text-tertiary outline-none focus-visible:border-ring',
);

type RowActions = {
  busy: string | null;
  confirmDelete: string | null;
  test: (h: Webhook) => void;
  toggleActive: (h: Webhook) => void;
  askDelete: (id: string | null) => void;
  remove: (h: Webhook) => void;
};

const buildColumns = ({ busy, confirmDelete, test, toggleActive, askDelete, remove }: RowActions): ColumnDef<Webhook>[] => [
  {
    key: 'status',
    header: 'Status',
    accessor: (h) => (
      <span className="inline-flex items-center gap-2">
        <span className={cn('size-1.5 shrink-0', h.active ? 'bg-signal' : 'bg-text-tertiary')} />
        <span className={cn('font-mono text-2xs font-bold tracking-wider', h.active ? 'text-signal' : 'text-text-tertiary')}>
          {h.active ? 'ACTIVE' : 'PAUSED'}
        </span>
      </span>
    ),
    sortable: true,
    sortValue: (h) => (h.active ? 1 : 0),
    className: 'w-28',
  },
  {
    key: 'url',
    header: 'URL',
    accessor: (h) => <span className="font-mono text-card-foreground">{h.url}</span>,
    sortable: true,
    sortValue: (h) => h.url,
    truncate: true,
  },
  {
    key: 'events',
    header: 'Events',
    accessor: (h) => (
      <span className="flex flex-wrap items-center gap-1.5">
        {(h.events ?? []).map((ev) => (
          <span key={ev} className="border border-border bg-secondary px-1.5 py-0.5 font-mono text-2xs text-text-secondary">{ev}</span>
        ))}
      </span>
    ),
    filterValue: (h) => (h.events ?? []).join(' '),
    className: 'w-64',
  },
  {
    key: 'retries',
    header: 'Retries',
    accessor: (h) => h.retries,
    sortable: true,
    sortValue: (h) => h.retries,
    align: 'right',
    className: 'w-20',
  },
  {
    key: 'actions',
    header: '',
    align: 'right',
    className: 'w-56',
    accessor: (h) => confirmDelete === h.id ? (
      <span className="flex justify-end gap-1.5">
        <Button size="xs" variant="destructive" disabled={busy === h.id} onClick={() => remove(h)}>Confirm delete</Button>
        <Button size="xs" variant="ghost" onClick={() => askDelete(null)}>Cancel</Button>
      </span>
    ) : (
      <span className="flex justify-end gap-1.5">
        <Button size="xs" variant="outline" disabled={busy === h.id} aria-label={`Send test event to ${h.url}`} onClick={() => test(h)}>Test</Button>
        <Button size="xs" variant="outline" disabled={busy === h.id} aria-label={`${h.active ? 'Pause' : 'Resume'} ${h.url}`} onClick={() => toggleActive(h)}>
          {h.active ? 'Pause' : 'Resume'}
        </Button>
        <Button size="xs" variant="ghost" className="text-danger" disabled={busy === h.id} aria-label={`Delete ${h.url}`} onClick={() => askDelete(h.id)}>Delete</Button>
      </span>
    ),
  },
];

export default function WebhooksPanel() {
  const [hooks, setHooks] = useState<Webhook[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await webhookService.list();
      setHooks((resp.webhooks ?? []) as Webhook[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list webhooks.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const test = useCallback(async (h: Webhook) => {
    setBusy(h.id);
    setNotice(null);
    try {
      const { delivery } = await webhookService.test(h.id);
      setNotice(delivery.status === 'success'
        ? { ok: true, text: `Test event delivered to ${h.url} (HTTP ${delivery.http_status ?? '—'}).` }
        : { ok: false, text: `Test event to ${h.url} failed: ${delivery.error || `HTTP ${delivery.http_status ?? '—'}`}.` });
    } catch {
      setNotice({ ok: false, text: `Could not send a test event to ${h.url}.` });
    } finally {
      setBusy(null);
    }
  }, []);

  const toggleActive = useCallback(async (h: Webhook) => {
    setBusy(h.id);
    setNotice(null);
    try {
      await webhookService.update(h.id, { active: !h.active });
      await load();
    } catch {
      setNotice({ ok: false, text: `Could not ${h.active ? 'pause' : 'resume'} ${h.url}.` });
    } finally {
      setBusy(null);
    }
  }, [load]);

  const remove = useCallback(async (h: Webhook) => {
    setBusy(h.id);
    setConfirmDelete(null);
    setNotice(null);
    try {
      await webhookService.remove(h.id);
      await load();
    } catch {
      setNotice({ ok: false, text: `Could not delete ${h.url}.` });
    } finally {
      setBusy(null);
    }
  }, [load]);

  const columns = useMemo(
    () => buildColumns({ busy, confirmDelete, test, toggleActive, askDelete: setConfirmDelete, remove }),
    [busy, confirmDelete, test, toggleActive, remove],
  );

  // ── register ──
  const [creating, setCreating] = useState(false);
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [eventTypes, setEventTypes] = useState<string[] | null>(null);
  const [eventTypesError, setEventTypesError] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const openCreate = useCallback(async () => {
    setCreating(true);
    setFormError(null);
    setEventTypesError(false);
    try {
      setEventTypes((await webhookService.events()).events ?? []);
    } catch {
      setEventTypes([]);
      setEventTypesError(true);
    }
  }, []);

  const togglePicked = (ev: string) =>
    setPicked((prev) => (prev.includes(ev) ? prev.filter((x) => x !== ev) : [...prev, ev]));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!/^https?:\/\//i.test(url.trim())) { setFormError('Enter the endpoint URL, starting with http:// or https://.'); return; }
    if (picked.length === 0) { setFormError('Pick at least one event to send.'); return; }
    setSaving(true);
    setFormError(null);
    try {
      await webhookService.create({ url: url.trim(), events: picked, description: description.trim() || undefined });
      setUrl(''); setDescription(''); setPicked([]); setCreating(false);
      await load();
    } catch (err) {
      setFormError(err instanceof Error && err.message ? err.message : 'Could not register the webhook.');
    } finally {
      setSaving(false);
    }
  };

  const count = hooks?.length ?? 0;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-webhooks-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {hooks === null && !error ? 'loading…' : `${count} outbound webhook${count === 1 ? '' : 's'} · HMAC-signed`}
        </span>
        <div className="flex-1" />
        <Button size="sm" onClick={() => (creating ? setCreating(false) : openCreate())} aria-expanded={creating}>
          <Plus /> New webhook
        </Button>
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {creating && (
        <form onSubmit={submit} className="flex flex-col gap-2 border border-border bg-card p-3" data-testid="wb-webhook-new">
          <label htmlFor="wb-webhook-url" className="font-mono text-2xs text-text-tertiary">Endpoint URL</label>
          <input id="wb-webhook-url" name="url" type="url" value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/hooks/aura" aria-invalid={!!formError && !/^https?:\/\//i.test(url.trim())} className={FIELD} />
          <label htmlFor="wb-webhook-description" className="font-mono text-2xs text-text-tertiary">Description (optional)</label>
          <input id="wb-webhook-description" name="description" value={description} onChange={(e) => setDescription(e.target.value)} className={FIELD} />
          <fieldset className="flex flex-col gap-1 border border-border p-2">
            <legend className="px-1 font-mono text-2xs text-text-tertiary">Events ({picked.length} selected)</legend>
            {eventTypes === null && <span className="font-mono text-2xs text-text-tertiary">Loading event types…</span>}
            {eventTypesError && <span role="alert" className="font-mono text-2xs text-danger">Could not load the event types.</span>}
            {(eventTypes ?? []).map((ev) => (
              <label key={ev} className="flex items-center gap-2 font-mono text-xs text-card-foreground">
                <input type="checkbox" checked={picked.includes(ev)} onChange={() => togglePicked(ev)} />
                {ev}
              </label>
            ))}
          </fieldset>
          {formError && <div role="alert" className="font-mono text-xs text-danger">{formError}</div>}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={saving}>{saving ? 'Registering…' : 'Register webhook'}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </form>
      )}

      {notice && (
        <div role="status" className={cn('border border-border bg-secondary px-3 py-1.5 font-mono text-xs', notice.ok ? 'text-signal' : 'text-danger')}>
          {notice.text}
        </div>
      )}

      {error && hooks !== null && (
        <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>
      )}

      <DataTable
        columns={columns}
        rows={hooks}
        error={error}
        onRetry={load}
        errorTitle="Unavailable"
        emptyTitle="No webhooks configured"
        emptyDescription="Use New webhook to register an endpoint that receives HMAC-signed events."
        filterPlaceholder="Filter webhooks…"
        getRowKey={(h) => h.id}
      />

      <InboundHooksSection />
    </div>
  );
}
