/* Cost — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real LLM token accounting from
   GET /llm-stats via costService. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
import { costService } from '../../services/api';

type Row = { provider: string; model: string; kind: string; tokens: number };
type Breakdown = { available: boolean; rows: Row[]; totals: { prompt: number; completion: number; cached_completion: number } };

function fmt(n: number): string {
  if (!n) return '0';
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}

const columns: ColumnDef<Row>[] = [
  {
    key: 'provider',
    header: 'Provider',
    accessor: (r) => <span className="text-text-secondary">{r.provider}</span>,
    sortable: true,
    sortValue: (r) => r.provider,
  },
  {
    key: 'model',
    header: 'Model',
    accessor: (r) => <span className="text-card-foreground">{r.model}</span>,
    sortable: true,
    sortValue: (r) => r.model,
  },
  {
    key: 'kind',
    header: 'Kind',
    accessor: (r) => r.kind,
    align: 'right',
    className: 'w-28',
  },
  {
    key: 'tokens',
    header: 'Tokens',
    accessor: (r) => <span className="text-signal">{fmt(r.tokens)}</span>,
    sortable: true,
    sortValue: (r) => r.tokens,
    align: 'right',
    className: 'w-28',
  },
];

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <Panel className="min-w-[150px] flex-1 p-4">
      <div className="font-mono text-2xs font-semibold uppercase tracking-widest text-text-tertiary">{label}</div>
      <div className="mt-1.5 text-3xl font-semibold text-card-foreground">{value}</div>
      <div className="mt-0.5 font-mono text-2xs text-text-tertiary">{sub}</div>
    </Panel>
  );
}

export default function CostPanel() {
  const [data, setData] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = (await costService.breakdown()) as Breakdown;
      setData(resp);
      setError(null);
    } catch {
      setError('Could not reach the gateway to load token accounting.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const t = data?.totals;
  const rows = data?.rows ?? [];

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-cost-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {data === null && !error ? 'loading…' : `LLM token accounting · ${data?.available ? 'live' : 'unavailable'}`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && data !== null && (
        <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>
      )}

      <div className="flex flex-wrap gap-3">
        <Tile label="Prompt tokens" value={fmt(t?.prompt ?? 0)} sub="input" />
        <Tile label="Completion tokens" value={fmt(t?.completion ?? 0)} sub="output" />
        <Tile label="Cached" value={fmt(t?.cached_completion ?? 0)} sub="reused completions" />
      </div>

      <DataTable
        columns={columns}
        rows={data === null ? null : rows}
        error={error}
        onRetry={load}
        errorTitle="Unavailable"
        emptyTitle="No usage yet"
        emptyDescription="Run a query or an audit and per-model usage appears here."
        filterPlaceholder="Filter usage…"
        getRowKey={(r, i) => `${r.provider}-${r.model}-${r.kind}-${i}`}
      />
    </div>
  );
}
