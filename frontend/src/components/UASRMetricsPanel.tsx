/**
 * UASRMetricsPanel — live view of UASR self-healing metrics.
 *
 * Subscribes to SSE topic `uasr:metrics` (populated every few seconds by the
 * api_gateway poller that reads UASR's /uasr/metrics). Renders:
 *   - current Hᵤ score (radial gauge)
 *   - resolution rate trend (line)
 *   - per-source healing contribution (top 5)
 */
import { useCallback, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, RadialBarChart, RadialBar, PolarAngleAxis,
} from 'recharts';
import { useSSE, type SSEEvent } from '../hooks/useSSE';
import Card, { CardHeader, CardBody } from './ui/Card';

const MAX_HISTORY = 30;

interface UASRSnapshot {
  hu_score: number;
  total_sources: number;
  total_events: number;
  resolved_events: number;
  global_resolution_rate: number;
  global_avg_latency: number;
  trend: string;
  per_source: Array<{
    source_id: string;
    healing_contribution: number;
    resolution_rate: number;
    total_events: number;
  }>;
}

interface HistoryPoint {
  ts: string;
  hu: number;
  resolution: number;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { minute: '2-digit', second: '2-digit' });
  } catch { return iso; }
}

export default function UASRMetricsPanel() {
  const [latest, setLatest] = useState<UASRSnapshot | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);

  const handleEvent = useCallback((event: SSEEvent) => {
    if (event.type !== 'data') return;
    const snap = event.payload as UASRSnapshot;
    setLatest(snap);
    setHistory((prev) => [
      ...prev.slice(-(MAX_HISTORY - 1)),
      {
        ts: event.timestamp,
        hu: Math.round((snap.hu_score ?? 0) * 100),
        resolution: Math.round((snap.global_resolution_rate ?? 0) * 100),
      },
    ]);
  }, []);

  useSSE({ topic: 'uasr:metrics', onEvent: handleEvent });

  const hu = latest ? Math.round(latest.hu_score * 100) : 0;
  const huColor =
    hu >= 75 ? 'var(--fg-green)' :
    hu >= 50 ? 'var(--fg-yellow)' :
               'var(--fg-red)';

  const topSources = (latest?.per_source || [])
    .slice()
    .sort((a, b) => b.healing_contribution - a.healing_contribution)
    .slice(0, 5);

  return (
    <Card>
      <CardHeader
        title="UASR Self-Healing"
        subtitle={latest
          ? `Hᵤ ${hu}% · ${latest.resolved_events}/${latest.total_events} events resolved · ${latest.total_sources} sources`
          : 'Waiting for metrics…'}
      />
      <CardBody>
        <div className="aura-split aura-split--metric">
          {/* Hᵤ gauge */}
          <div style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <RadialBarChart
                innerRadius="70%"
                outerRadius="100%"
                data={[{ name: 'hu', value: hu, fill: huColor }]}
                startAngle={90}
                endAngle={-270}
              >
                <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
                <RadialBar background dataKey="value" cornerRadius={8} />
                <text
                  x="50%" y="48%" textAnchor="middle" dominantBaseline="middle"
                  style={{
                    fontSize: 28, fontWeight: 700, fill: 'var(--text-primary)',
                    fontFamily: 'var(--font-mono)',
                  }}
                >
                  {hu}%
                </text>
                <text
                  x="50%" y="64%" textAnchor="middle" dominantBaseline="middle"
                  style={{ fontSize: 11, fill: 'var(--text-tertiary)', letterSpacing: '0.07em' }}
                >
                  Hᵤ SCORE
                </text>
              </RadialBarChart>
            </ResponsiveContainer>
          </div>

          {/* Trend chart */}
          <div style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={history} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                <XAxis dataKey="ts" tickFormatter={formatTime} tick={{ fontSize: 10, fill: 'var(--text-tertiary)' }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: 'var(--text-tertiary)' }} />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-md)',
                    fontSize: 12,
                  }}
                  labelFormatter={(v) => formatTime(v as string)}
                />
                <Line type="monotone" dataKey="hu" stroke="var(--chart-1, #3b82f6)" strokeWidth={2} dot={false} name="Hᵤ %" />
                <Line type="monotone" dataKey="resolution" stroke="var(--chart-2, #22c55e)" strokeWidth={2} dot={false} name="Resolved %" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Per-source contributions */}
        {topSources.length > 0 && (
          <div style={{
            marginTop: 'var(--space-4)',
            borderTop: '1px solid var(--border-subtle)',
            paddingTop: 'var(--space-3)',
            display: 'flex', flexDirection: 'column', gap: 'var(--space-2)',
          }}>
            <div style={{
              fontSize: 10, fontWeight: 600, letterSpacing: '0.07em',
              textTransform: 'uppercase', color: 'var(--text-tertiary)',
            }}>
              Top sources by healing contribution
            </div>
            {topSources.map((s) => {
              const pct = Math.round(s.healing_contribution * 100);
              return (
                <div key={s.source_id} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 'var(--font-xs)',
                    color: 'var(--text-secondary)', minWidth: 120,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {s.source_id}
                  </span>
                  <div style={{
                    flex: 1, height: 6, background: 'var(--bg-elevated)',
                    borderRadius: 'var(--radius-full)', overflow: 'hidden',
                  }}>
                    <div style={{
                      height: '100%', width: `${pct}%`,
                      background: 'var(--fg-indigo, #6366f1)',
                      transition: 'width var(--t-hover, 150ms) ease',
                    }} />
                  </div>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 'var(--font-xs)',
                    color: 'var(--text-tertiary)', minWidth: 40, textAlign: 'right',
                  }}>
                    {pct}%
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {latest && (
          <div style={{
            marginTop: 'var(--space-3)',
            fontSize: 'var(--font-xs)', fontFamily: 'var(--font-mono)',
            color: 'var(--text-tertiary)',
            display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap',
          }}>
            <span>avg latency: {latest.global_avg_latency?.toFixed(2)}s</span>
            <span>trend: {latest.trend}</span>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
