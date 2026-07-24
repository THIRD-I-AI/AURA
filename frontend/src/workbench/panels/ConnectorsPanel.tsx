/* Connectors — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real data sources from
   GET /connections via connectorService: database connections + file sources. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { cn } from '@/lib/cn';
import { connectorService } from '../../services/api';

type Connection = { id?: string; name?: string; type?: string; source_id?: string; status?: string };
type SourcesResp = { connections?: Connection[]; count?: number; file_sources?: number };

const sectionLabel = 'border-b border-border px-4 py-2.5 font-mono text-2xs font-semibold uppercase tracking-widest text-text-tertiary';

export default function ConnectorsPanel() {
  const [data, setData] = useState<SourcesResp | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = (await connectorService.listSources()) as SourcesResp;
      setData(resp);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list connectors.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const conns = data?.connections ?? [];
  const fileSources = data?.file_sources ?? 0;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-connectors-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {data === null ? 'loading…' : `${conns.length} database connection${conns.length === 1 ? '' : 's'} · ${fileSources} file source${fileSources === 1 ? '' : 's'}`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <Panel>
        <div className={sectionLabel}>Database connections</div>
        {data === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading connectors…</div>}
        {data !== null && conns.length === 0 && !error && (
          <div className="px-4 py-4 text-sm leading-relaxed text-text-tertiary">
            No external database connections. Add PostgreSQL, MySQL, or BigQuery to query live warehouses alongside your files.
          </div>
        )}
        {conns.map((c, i) => (
          <div key={c.id || c.source_id || i} className="flex items-center gap-2.5 border-t border-border px-4 py-2.5">
            <span className={cn('size-1.5 shrink-0', c.status === 'connected' ? 'bg-signal' : 'bg-warn')} />
            <span className="text-sm text-card-foreground">{c.name || c.source_id || '(source)'}</span>
            <div className="flex-1" />
            <span className="font-mono text-2xs font-semibold tracking-wider text-text-tertiary">{(c.type || 'db').toUpperCase()}</span>
          </div>
        ))}
      </Panel>

      <Panel>
        <div className="flex items-center gap-2 px-4 py-2.5">
          <div className="font-mono text-2xs font-semibold uppercase tracking-widest text-text-tertiary">File sources</div>
          <div className="flex-1" />
          <span className="font-mono text-2xs text-signal">{fileSources} active</span>
        </div>
        <div className="px-4 pb-3.5 text-xs leading-relaxed text-text-tertiary">
          Uploaded datasets are auto-registered as queryable sources — manage them in <span className="text-text-secondary">Files &amp; Data</span>.
        </div>
      </Panel>
    </div>
  );
}
