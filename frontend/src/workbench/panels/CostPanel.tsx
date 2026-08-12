/* Cost — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real LLM token accounting from
   GET /llm-stats via costService. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { costService } from '../../services/api';

type Row = { provider: string; model: string; kind: string; tokens: number };
type Breakdown = { available: boolean; rows: Row[]; totals: { prompt: number; completion: number; cached_completion: number } };

function fmt(n: number): string {
  if (!n) return '0';
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}

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
          {data === null ? 'loading…' : `LLM token accounting · ${data.available ? 'live' : 'unavailable'}`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <div className="flex flex-wrap gap-3">
        <Tile label="Prompt tokens" value={fmt(t?.prompt ?? 0)} sub="input" />
        <Tile label="Completion tokens" value={fmt(t?.completion ?? 0)} sub="output" />
        <Tile label="Cached" value={fmt(t?.cached_completion ?? 0)} sub="reused completions" />
      </div>

      <Panel>
        <div className="grid grid-cols-[1fr_1fr_100px_110px] border-b border-border px-4 py-2.5 font-mono text-2xs font-semibold uppercase tracking-widest text-text-tertiary">
          <span>Provider</span><span>Model</span><span className="text-right">Kind</span><span className="text-right">Tokens</span>
        </div>
        {data === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading…</div>}
        {data !== null && rows.length === 0 && !error && (
          <div className="px-4 py-5 text-center text-sm leading-relaxed text-text-tertiary">
            No token usage recorded yet. Run a query or an audit and per-model usage appears here.
          </div>
        )}
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-[1fr_1fr_100px_110px] items-center border-t border-border px-4 py-2.5">
            <div className="text-xs text-text-secondary">{r.provider}</div>
            <div className="text-xs text-card-foreground">{r.model}</div>
            <div className="text-right font-mono text-2xs text-text-tertiary">{r.kind}</div>
            <div className="text-right font-mono text-xs text-signal">{fmt(r.tokens)}</div>
          </div>
        ))}
      </Panel>
    </div>
  );
}
