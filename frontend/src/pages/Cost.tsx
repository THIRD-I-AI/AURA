import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { costService, type LlmTokenBreakdown, type LlmTokenRow } from '../services/api';

const fmt = (n: number) => Math.round(n).toLocaleString();

const Cost: React.FC = () => {
  const [data, setData] = useState<LlmTokenBreakdown | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const snap = await costService.breakdown();
      setData(snap);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load LLM cost data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = window.setInterval(refresh, 15000);
    return () => window.clearInterval(id);
  }, [autoRefresh, refresh]);

  const byProviderModel = useMemo(() => {
    if (!data) return [];
    const map = new Map<string, { provider: string; model: string; prompt: number; completion: number; cached: number }>();
    for (const row of data.rows) {
      const key = `${row.provider}::${row.model}`;
      const existing = map.get(key) ?? { provider: row.provider, model: row.model, prompt: 0, completion: 0, cached: 0 };
      if (row.kind === 'prompt') existing.prompt += row.tokens;
      else if (row.kind === 'completion') existing.completion += row.tokens;
      else if (row.kind === 'cached_completion') existing.cached += row.tokens;
      map.set(key, existing);
    }
    return Array.from(map.values()).sort((a, b) => (b.prompt + b.completion) - (a.prompt + a.completion));
  }, [data]);

  const totals = data?.totals ?? { prompt: 0, completion: 0, cached_completion: 0 };
  const grandTotal = totals.prompt + totals.completion;

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h2 style={{ margin: 0 }}>LLM Token Usage</h2>
          <p style={{ margin: '4px 0 0', color: 'var(--color-text-muted, #94a3b8)', fontSize: 13 }}>
            Live in-process counter. Resets on service restart.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh (15s)
          </label>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            style={{
              padding: '6px 14px',
              border: '1px solid var(--color-border, #334155)',
              background: 'transparent',
              color: 'inherit',
              borderRadius: 6,
              cursor: loading ? 'wait' : 'pointer',
            }}
          >
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && (
        <div style={{ padding: 12, border: '1px solid #b91c1c', background: 'rgba(185, 28, 28, 0.1)', color: '#fca5a5', borderRadius: 8 }}>
          {error}
        </div>
      )}

      {data && !data.available && (
        <div style={{ padding: 12, border: '1px solid #92400e', background: 'rgba(146, 64, 14, 0.1)', color: '#fcd34d', borderRadius: 8 }}>
          Token counters are unavailable in this environment. Install <code>prometheus-client</code> in the gateway service to enable.
        </div>
      )}

      {/* KPI tiles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
        <KpiTile label="Prompt tokens" value={fmt(totals.prompt)} accent="#60a5fa" />
        <KpiTile label="Completion tokens" value={fmt(totals.completion)} accent="#34d399" />
        <KpiTile label="Cached (subset)" value={fmt(totals.cached_completion)} accent="#a78bfa" />
        <KpiTile label="Total tokens" value={fmt(grandTotal)} accent="#fbbf24" />
      </div>

      {/* Per-provider/model table */}
      <div style={{ border: '1px solid var(--color-border, #1f2937)', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: '10px 14px', background: 'var(--color-surface-2, #0b1220)', fontWeight: 600 }}>
          By provider / model
        </div>
        {byProviderModel.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--color-text-muted, #94a3b8)' }}>
            No tokens recorded yet. Send a chat message to populate this view.
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: 'var(--color-surface-2, #0b1220)' }}>
                <Th>Provider</Th>
                <Th>Model</Th>
                <Th align="right">Prompt</Th>
                <Th align="right">Completion</Th>
                <Th align="right">Cached</Th>
                <Th align="right">Total</Th>
                <Th align="right">Share</Th>
              </tr>
            </thead>
            <tbody>
              {byProviderModel.map((row) => {
                const total = row.prompt + row.completion;
                const share = grandTotal > 0 ? (total / grandTotal) * 100 : 0;
                return (
                  <tr key={`${row.provider}::${row.model}`} style={{ borderTop: '1px solid var(--color-border, #1f2937)' }}>
                    <Td>{row.provider}</Td>
                    <Td><code style={{ fontSize: 12 }}>{row.model || '—'}</code></Td>
                    <Td align="right">{fmt(row.prompt)}</Td>
                    <Td align="right">{fmt(row.completion)}</Td>
                    <Td align="right">{fmt(row.cached)}</Td>
                    <Td align="right" bold>{fmt(total)}</Td>
                    <Td align="right">
                      <ShareBar pct={share} />
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Raw rows for debugging */}
      {data?.rows && data.rows.length > 0 && (
        <details>
          <summary style={{ cursor: 'pointer', color: 'var(--color-text-muted, #94a3b8)', fontSize: 13 }}>
            Raw counter rows ({data.rows.length})
          </summary>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginTop: 8 }}>
            <thead>
              <tr style={{ background: 'var(--color-surface-2, #0b1220)' }}>
                <Th>Provider</Th><Th>Model</Th><Th>Kind</Th><Th align="right">Tokens</Th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r: LlmTokenRow, i: number) => (
                <tr key={i} style={{ borderTop: '1px solid var(--color-border, #1f2937)' }}>
                  <Td>{r.provider}</Td>
                  <Td><code>{r.model || '—'}</code></Td>
                  <Td>{r.kind}</Td>
                  <Td align="right">{fmt(r.tokens)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
};

const KpiTile: React.FC<{ label: string; value: string; accent: string }> = ({ label, value, accent }) => (
  <div style={{ padding: 14, border: '1px solid var(--color-border, #1f2937)', borderRadius: 8, background: 'var(--color-surface, #0f172a)' }}>
    <div style={{ fontSize: 12, color: 'var(--color-text-muted, #94a3b8)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
    <div style={{ fontSize: 24, fontWeight: 700, marginTop: 6, color: accent }}>{value}</div>
  </div>
);

const Th: React.FC<{ children: React.ReactNode; align?: 'left' | 'right' }> = ({ children, align = 'left' }) => (
  <th style={{ padding: '8px 12px', textAlign: align, fontWeight: 600, fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.4, color: 'var(--color-text-muted, #94a3b8)' }}>{children}</th>
);

const Td: React.FC<{ children: React.ReactNode; align?: 'left' | 'right'; bold?: boolean }> = ({ children, align = 'left', bold }) => (
  <td style={{ padding: '8px 12px', textAlign: align, fontWeight: bold ? 700 : 400 }}>{children}</td>
);

const ShareBar: React.FC<{ pct: number }> = ({ pct }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'flex-end' }}>
    <div style={{ width: 60, height: 6, background: 'var(--color-border, #1f2937)', borderRadius: 3, overflow: 'hidden' }}>
      <div style={{ width: `${Math.min(100, pct)}%`, height: '100%', background: '#60a5fa' }} />
    </div>
    <span style={{ fontVariantNumeric: 'tabular-nums', minWidth: 38, textAlign: 'right' }}>{pct.toFixed(1)}%</span>
  </div>
);

export default Cost;
