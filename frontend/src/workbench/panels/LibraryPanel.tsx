/* Library — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Lists real saved queries from
   GET /saved-queries via savedQueryService. Read + star; full editing stays in
   the query flow that created them. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, Star } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { savedQueryService } from '../../services/api';

type SavedQuery = { id: string; name?: string; sql?: string; prompt?: string; starred?: boolean };

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

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <Panel>
        {items === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading library…</div>}
        {items !== null && count === 0 && !error && (
          <EmptyState intent="empty" title="No saved queries yet" description="Save a query from Ask AURA and it appears here for one-click reuse." />
        )}
        {(items ?? []).map((q, i) => (
          <div key={q.id} className={cn('flex flex-col gap-1.5 px-4 py-3', i > 0 && 'border-t border-border')}>
            <div className="flex items-center gap-2.5">
              <Star className={cn('size-3.5 shrink-0', q.starred ? 'fill-warn text-warn' : 'text-text-tertiary')} />
              <span className="truncate text-sm font-semibold text-card-foreground">{q.name || '(untitled)'}</span>
              <div className="flex-1" />
              {q.prompt && q.prompt !== q.name && (
                <span className="max-w-[320px] truncate text-xs text-text-tertiary">{q.prompt}</span>
              )}
            </div>
            {q.sql && (
              <div className="truncate border border-border bg-secondary px-2.5 py-1.5 font-mono text-2xs text-text-secondary">{q.sql}</div>
            )}
          </div>
        ))}
      </Panel>
    </div>
  );
}
