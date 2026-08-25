/* Lineage — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real data-lineage graph from
   GET /lineage via lineageService: which tables feed which saved queries and
   dashboards. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
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

function typeMeta(type: Node['type']) {
  return TYPE_META[type] ?? { label: String(type).toUpperCase(), dot: 'bg-text-tertiary', text: 'text-text-tertiary' };
}

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
  const edges = useMemo(() => graph?.edges ?? [], [graph]);
  const s = graph?.summary;
  const downstream = useCallback((id: string) => edges.filter((e) => e.source === id).length, [edges]);

  const columns = useMemo<ColumnDef<Node>[]>(() => [
    {
      key: 'type',
      header: 'Type',
      accessor: (n) => {
        const meta = typeMeta(n.type);
        return (
          <div className="flex items-center gap-2">
            <span className={cn('size-1.5 shrink-0', meta.dot)} />
            <span className={cn('font-mono text-2xs font-semibold tracking-wider', meta.text)}>{meta.label}</span>
          </div>
        );
      },
      sortable: true,
      sortValue: (n) => typeMeta(n.type).label,
    },
    {
      key: 'node',
      header: 'Node',
      accessor: (n) => <span className="text-sm text-card-foreground">{n.label}</span>,
      sortable: true,
      sortValue: (n) => n.label,
      filterValue: (n) => n.label,
      truncate: true,
    },
    {
      key: 'downstream',
      header: 'Downstream',
      accessor: (n) => {
        const dn = downstream(n.id);
        return <span className={dn ? 'text-text-secondary' : 'text-text-tertiary'}>{dn ? `${dn} →` : '—'}</span>;
      },
      sortable: true,
      sortValue: (n) => downstream(n.id),
      align: 'right',
    },
  ], [downstream]);

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

      {error && graph !== null && (
        <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>
      )}

      {s && (
        <div className="flex flex-wrap gap-3">
          <Tile label="Tables" value={s.tables} />
          <Tile label="Queries" value={s.queries} />
          <Tile label="Dashboards" value={s.dashboards} />
          <Tile label="Edges" value={s.edges} />
        </div>
      )}

      <DataTable
        columns={columns}
        rows={graph === null ? null : nodes}
        error={error}
        onRetry={load}
        errorTitle="Unavailable"
        emptyTitle="No lineage yet"
        emptyDescription="Run queries and pin dashboards — AURA traces which datasets feed which results."
        filterPlaceholder="Filter lineage…"
        getRowKey={(n) => n.id}
      />
    </div>
  );
}
