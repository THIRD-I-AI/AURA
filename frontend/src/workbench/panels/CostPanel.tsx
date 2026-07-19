/* Cost — native terminal-authority panel (replaces embedded classic Cost page).
   Real LLM token accounting from GET /llm-stats via costService, styled to match
   the Cockpit. */
import { useCallback, useEffect, useState } from 'react';
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
    <div className="aw-panel" style={{ flex: 1, minWidth: 150, padding: '14px 16px' }}>
      <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.12em', color: 'var(--text3)' }}>{label}</div>
      <div className="aw-display" style={{ fontSize: 26, fontWeight: 600, color: 'var(--text)', marginTop: 6 }}>{value}</div>
      <div className="aw-mono" style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>
    </div>
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
    <div data-testid="wb-cost-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {data === null ? 'loading…' : `LLM token accounting · ${data.available ? 'live' : 'unavailable'}`}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={load} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '7px 14px' }}>↻ REFRESH</button>
      </div>

      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Tile label="PROMPT TOKENS" value={fmt(t?.prompt ?? 0)} sub="input" />
        <Tile label="COMPLETION TOKENS" value={fmt(t?.completion ?? 0)} sub="output" />
        <Tile label="CACHED" value={fmt(t?.cached_completion ?? 0)} sub="reused completions" />
      </div>

      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 100px 110px', padding: '10px 16px', borderBottom: '1px solid var(--hair)' }}>
          {['PROVIDER', 'MODEL', 'KIND', 'TOKENS'].map((h, i) => (
            <div key={h} className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.12em', color: 'var(--text3)', textAlign: i >= 2 ? 'right' : 'left' }}>{h}</div>
          ))}
        </div>
        {data === null && <div style={{ padding: '14px 16px', fontSize: 11.5, color: 'var(--text3)' }}>Loading…</div>}
        {data !== null && rows.length === 0 && !error && (
          <div style={{ padding: '20px 16px', fontSize: 12, color: 'var(--text3)', textAlign: 'center', lineHeight: 1.7 }}>
            No token usage recorded yet.<br />Run a query or an audit and per-model usage appears here.
          </div>
        )}
        {rows.map((r, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 100px 110px', alignItems: 'center', padding: '9px 16px', borderTop: '1px solid var(--hair)' }}>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>{r.provider}</div>
            <div style={{ fontSize: 12, color: 'var(--text)' }}>{r.model}</div>
            <div className="aw-mono" style={{ fontSize: 10, color: 'var(--text3)', textAlign: 'right' }}>{r.kind}</div>
            <div className="aw-mono" style={{ fontSize: 11, color: 'var(--accent)', textAlign: 'right' }}>{fmt(r.tokens)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
