/* Streaming — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real streaming pipelines from
   GET /streaming/pipelines via streamingService. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { streamingService } from '../../services/api';

type Pipeline = {
  id: string; name?: string; description?: string; status?: string;
  event_time_field?: string; watermark_delay_seconds?: number;
  sinks?: unknown[]; transforms?: unknown[];
};

function statusTone(s?: string): { dot: string; text: string } {
  const v = (s || '').toLowerCase();
  if (v === 'running' || v === 'active') return { dot: 'bg-signal', text: 'text-signal' };
  if (v === 'error' || v === 'failed') return { dot: 'bg-danger', text: 'text-danger' };
  if (v === 'paused' || v === 'stopped') return { dot: 'bg-warn', text: 'text-warn' };
  return { dot: 'bg-text-tertiary', text: 'text-text-tertiary' };
}

export default function StreamingPanel() {
  const [items, setItems] = useState<Pipeline[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await streamingService.list();
      setItems((resp.pipelines ?? []) as Pipeline[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list streaming pipelines.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const count = items?.length ?? 0;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-streaming-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {items === null ? 'loading…' : `${count} streaming pipeline${count === 1 ? '' : 's'} · watermark-driven, self-healing`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <Panel>
        {items === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading pipelines…</div>}
        {items !== null && count === 0 && !error && (
          <EmptyState intent="empty" title="No streaming pipelines yet" description="Define one over a Kafka/Redpanda topic — MAPE-K drift repair keeps it self-healing." />
        )}
        {(items ?? []).map((p, i) => {
          const tone = statusTone(p.status);
          return (
            <div key={p.id} className={cn('flex flex-col gap-1.5 px-4 py-3', i > 0 && 'border-t border-border')}>
              <div className="flex items-center gap-2.5">
                <span className={cn('size-1.5 shrink-0', tone.dot)} />
                <span className="truncate text-sm font-semibold text-card-foreground">{p.name || p.id}</span>
                <div className="flex-1" />
                <span className={cn('font-mono text-2xs font-bold tracking-wider', tone.text)}>{(p.status || 'idle').toUpperCase()}</span>
              </div>
              <div className="font-mono text-2xs text-text-tertiary">
                {(p.sinks?.length ?? 0)} sink{(p.sinks?.length ?? 0) === 1 ? '' : 's'} · {(p.transforms?.length ?? 0)} transform{(p.transforms?.length ?? 0) === 1 ? '' : 's'}
                {typeof p.watermark_delay_seconds === 'number' && ` · watermark ${p.watermark_delay_seconds}s`}
                {p.event_time_field && ` · event-time ${p.event_time_field}`}
              </div>
            </div>
          );
        })}
      </Panel>
    </div>
  );
}
