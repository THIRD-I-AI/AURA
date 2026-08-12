/* Dashboards — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real saved dashboards from
   GET /dashboards via dashboardService. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { dashboardService } from '../../services/api';

type Tile = { id?: string };
type Dashboard = { id: string; name?: string; description?: string | null; tiles?: Tile[]; updated_at?: string };

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

  const count = items?.length ?? 0;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-dashboards-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {items === null ? 'loading…' : `${count} dashboard${count === 1 ? '' : 's'} · this workspace`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      {items !== null && count === 0 && !error ? (
        <Panel>
          <EmptyState intent="empty" title="No dashboards yet" description="Pin a query result from Ask AURA to build a live dashboard of your workspace metrics." />
        </Panel>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(min(260px,100%),1fr))] gap-3">
          {items === null && <Panel className="p-4 text-xs text-text-tertiary">Loading…</Panel>}
          {(items ?? []).map((d) => (
            <Panel key={d.id} className="flex cursor-pointer flex-col gap-1.5 p-4 transition-colors hover:border-signal">
              <div className="flex items-center gap-2">
                <span className="size-1.5 shrink-0 bg-signal" />
                <span className="truncate text-sm font-semibold text-card-foreground">{d.name || '(untitled)'}</span>
              </div>
              {d.description && <div className="text-xs leading-snug text-text-tertiary">{d.description}</div>}
              <div className="mt-0.5 font-mono text-2xs text-text-tertiary">{(d.tiles?.length ?? 0)} tile{(d.tiles?.length ?? 0) === 1 ? '' : 's'}</div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
