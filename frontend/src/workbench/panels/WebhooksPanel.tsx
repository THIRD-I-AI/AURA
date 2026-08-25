/* Webhooks — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real outbound webhooks from
   GET /webhooks via webhookService. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
import { cn } from '@/lib/cn';
import { webhookService } from '../../services/api';

type Webhook = { id: string; url: string; events: string[]; active: boolean; retries: number; description?: string };

const columns: ColumnDef<Webhook>[] = [
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

  const count = hooks?.length ?? 0;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-webhooks-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {hooks === null && !error ? 'loading…' : `${count} outbound webhook${count === 1 ? '' : 's'} · HMAC-signed`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

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
        emptyDescription="Register an endpoint to receive HMAC-signed events — audit sealed, drift healed, pipeline completed."
        filterPlaceholder="Filter webhooks…"
        getRowKey={(h) => h.id}
      />
    </div>
  );
}
