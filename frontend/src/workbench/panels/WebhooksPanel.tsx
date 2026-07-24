/* Webhooks — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real outbound webhooks from
   GET /webhooks via webhookService. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { webhookService } from '../../services/api';

type Webhook = { id: string; url: string; events: string[]; active: boolean; retries: number; description?: string };

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
          {hooks === null ? 'loading…' : `${count} outbound webhook${count === 1 ? '' : 's'} · HMAC-signed`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <Panel>
        {hooks === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading webhooks…</div>}
        {hooks !== null && count === 0 && !error && (
          <EmptyState intent="empty" title="No webhooks configured" description="Register an endpoint to receive HMAC-signed events — audit sealed, drift healed, pipeline completed." />
        )}
        {(hooks ?? []).map((h, i) => (
          <div key={h.id} className={cn('flex flex-col gap-2 px-4 py-3', i > 0 && 'border-t border-border')}>
            <div className="flex items-center gap-2.5">
              <span className={cn('size-1.5 shrink-0', h.active ? 'bg-signal' : 'bg-text-tertiary')} />
              <span className="truncate font-mono text-xs text-card-foreground">{h.url}</span>
              <div className="flex-1" />
              <span className={cn('font-mono text-2xs font-bold tracking-wider', h.active ? 'text-signal' : 'text-text-tertiary')}>{h.active ? 'ACTIVE' : 'PAUSED'}</span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {(h.events ?? []).map((ev) => (
                <span key={ev} className="border border-border bg-secondary px-1.5 py-0.5 font-mono text-2xs text-text-secondary">{ev}</span>
              ))}
              <span className="font-mono text-2xs text-text-tertiary">retries {h.retries}</span>
            </div>
          </div>
        ))}
      </Panel>
    </div>
  );
}
