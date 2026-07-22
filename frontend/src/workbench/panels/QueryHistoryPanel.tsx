/* Query History — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md):
   ui-kit primitives + token utilities, no inline styles. Lists real executed
   queries from GET /query-history via analyticsService. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
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

export default function QueryHistoryPanel() {
  const [rows, setRows] = useState<QueryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

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

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <Panel>
        {rows === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading query history…</div>}
        {rows !== null && count === 0 && !error && (
          <EmptyState intent="empty" title="No queries yet" description="Ask a question in Ask AURA — it lands here with its generated SQL and status." />
        )}
        {(rows ?? []).map((q, i) => {
          const tone = statusTone(q.status);
          return (
            <div key={i} className={cn('flex flex-col gap-1.5 px-4 py-3', i > 0 && 'border-t border-border')}>
              <div className="flex items-center gap-2.5">
                <span className={cn('size-1.5 shrink-0', tone.dot)} />
                <span className="truncate text-sm text-card-foreground">{q.prompt || q.sql || '(query)'}</span>
                <div className="flex-1" />
                <span className={cn('font-mono text-2xs font-bold tracking-wider', tone.text)}>{(q.status || 'unknown').toUpperCase()}</span>
                {typeof q.row_count === 'number' && <span className="font-mono text-2xs text-text-tertiary">{q.row_count} rows</span>}
              </div>
              {q.sql && (
                <div className="truncate border border-border bg-secondary px-2.5 py-1.5 font-mono text-2xs text-text-secondary">{q.sql}</div>
              )}
            </div>
          );
        })}
      </Panel>
    </div>
  );
}
