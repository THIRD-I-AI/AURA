/* Lineage — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real data-lineage graph from
   GET /lineage via lineageService: which tables feed which saved queries and
   dashboards. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { lineageService } from '../../services/api';

type Node = { id: string; type: 'table' | 'saved_query' | 'dashboard'; label: string };
type Edge = { id: string; source: string; target: string };
type Graph = { nodes: Node[]; edges: Edge[]; summary?: { tables: number; queries: number; dashboards: number; edges: number } };

const TYPE_META: Record<Node['type'], { label: string; dot: string; text: string }> = {
  table: { label: 'TABLE', dot: 'bg-signal', text: 'text-signal' },
  saved_query: { label: 'QUERY', dot: 'bg-warn', text: 'text-warn' },
  dashboard: { label: 'DASHBOARD', dot: 'bg-info', text: 'text-info' },
};

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <Panel className="min-w-[120px] flex-1 p-4">
      <div className="font-mono text-2xs font-semibold uppercase tracking-widest text-text-tertiary">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-card-foreground">{value}</div>
    </Panel>
  );
}

export default function LineagePanel() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const g = (await lineageService.get()) as Graph;
      setGraph(g);
      setError(null);
    } catch {
      setError('Could not reach the gateway to load lineage.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const s = graph?.summary;
  const downstream = (id: string) => edges.filter((e) => e.source === id).length;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-lineage-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {graph === null ? 'loading…' : `${nodes.length} nodes · ${edges.length} edges · provenance graph`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      {s && (
        <div className="flex flex-wrap gap-3">
          <Tile label="Tables" value={s.tables} />
          <Tile label="Queries" value={s.queries} />
          <Tile label="Dashboards" value={s.dashboards} />
          <Tile label="Edges" value={s.edges} />
        </div>
      )}

      <Panel>
        <div className="grid grid-cols-[110px_1fr_130px] border-b border-border px-4 py-2.5 font-mono text-2xs font-semibold uppercase tracking-widest text-text-tertiary">
          <span>Type</span><span>Node</span><span className="text-right">Downstream</span>
        </div>
        {graph === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading lineage…</div>}
        {graph !== null && nodes.length === 0 && !error && (
          <EmptyState intent="empty" title="No lineage yet" description="Run queries and pin dashboards — AURA traces which datasets feed which results." />
        )}
        {nodes.map((n) => {
          const meta = TYPE_META[n.type] ?? { label: String(n.type).toUpperCase(), dot: 'bg-text-tertiary', text: 'text-text-tertiary' };
          const dn = downstream(n.id);
          return (
            <div key={n.id} className="grid grid-cols-[110px_1fr_130px] items-center border-t border-border px-4 py-2.5">
              <div className="flex items-center gap-2">
                <span className={cn('size-1.5 shrink-0', meta.dot)} />
                <span className={cn('font-mono text-2xs font-semibold tracking-wider', meta.text)}>{meta.label}</span>
              </div>
              <div className="truncate text-sm text-card-foreground">{n.label}</div>
              <div className={cn('text-right font-mono text-xs', dn ? 'text-text-secondary' : 'text-text-tertiary')}>{dn ? `${dn} →` : '—'}</div>
            </div>
          );
        })}
      </Panel>
    </div>
  );
}
