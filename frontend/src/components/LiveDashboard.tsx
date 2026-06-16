/**
 * LiveDashboard
 * ==============
 * Real-time analytics dashboard powered by SSE.
 * Displays query throughput, data quality score, healing coefficient (Hᵤ),
 * and active pipeline gauge using Recharts.
 *
 * Subscribes to:
 *   - system:health  — periodic health snapshots from the backend
 *   - monitor:*      — alert events from MonitorAgent
 */
import { useState, useEffect, useCallback } from 'react';
import {
  LineChart, Line, AreaChart, Area, RadialBarChart, RadialBar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  PolarAngleAxis,
} from 'recharts';
import { useSSE, type SSEEvent } from '../hooks/useSSE';
import Card, { CardHeader, CardBody } from './ui/Card';
import { ChartSkeleton } from './ui/Skeleton';
import { useToast } from '../contexts/ToastContext';
import UASRMetricsPanel from './UASRMetricsPanel';

const MAX_HISTORY = 30;

interface HealthSnapshot {
  ts: string;
  queryThroughput: number;
  healthyServices: number;
  totalServices: number;
  huScore: number | null;
}

interface QualityPoint {
  ts: string;
  quality: number;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso;
  }
}

export default function LiveDashboard() {
  const toast = useToast();

  const [healthHistory, setHealthHistory] = useState<HealthSnapshot[]>([]);
  const [qualityHistory, setQualityHistory] = useState<QualityPoint[]>([]);
  const [latestHealth, setLatestHealth] = useState<HealthSnapshot | null>(null);
  const [alertCount, setAlertCount] = useState(0);

  const handleHealthEvent = useCallback((event: SSEEvent) => {
    if (event.type !== 'data' && event.type !== 'progress') return;
    const p = event.payload as Record<string, unknown>;

    const snap: HealthSnapshot = {
      ts: event.timestamp,
      queryThroughput: (p.queries_run as number) ?? 0,
      healthyServices: (p.healthy_services as number) ?? 0,
      totalServices: (p.total_services as number) ?? 0,
      huScore: (p.hu_score as number | null) ?? null,
    };

    setLatestHealth(snap);
    setHealthHistory((prev) => [...prev.slice(-(MAX_HISTORY - 1)), snap]);

    // Quality derived from healthy fraction
    const q = snap.totalServices > 0
      ? Math.round((snap.healthyServices / snap.totalServices) * 100)
      : 100;
    setQualityHistory((prev) => [
      ...prev.slice(-(MAX_HISTORY - 1)),
      { ts: snap.ts, quality: q },
    ]);
  }, []);

  const handleMonitorEvent = useCallback((event: SSEEvent) => {
    if (event.type === 'error') {
      const p = event.payload as Record<string, unknown>;
      toast.warning(`Monitor alert: ${p.message ?? 'Unknown alert'}`, { duration: 6000 });
      setAlertCount((c) => c + 1);
    }
  }, [toast]);

  useSSE({ topic: 'system:health', onEvent: handleHealthEvent });
  useSSE({ topic: 'monitor:broadcast', onEvent: handleMonitorEvent });

  // Bootstrap — fetch current health immediately
  useEffect(() => {
    const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';
    fetch(`${API_BASE}/system/health`)
      .then((r) => r.json())
      .then((data) => {
        const snap: HealthSnapshot = {
          ts: new Date().toISOString(),
          queryThroughput: 0,
          healthyServices: data.healthy_services ?? 0,
          totalServices: data.total_services ?? 0,
          huScore: data.hu_score ?? null,
        };
        setLatestHealth(snap);
        setHealthHistory([snap]);
        setQualityHistory([{ ts: snap.ts, quality: snap.totalServices > 0 ? Math.round((snap.healthyServices / snap.totalServices) * 100) : 100 }]);
      })
      .catch(() => {/* offline — wait for SSE */});
  }, []);

  // ── Radial gauge data ──────────────────────────────────────────────
  const healthPct = latestHealth && latestHealth.totalServices > 0
    ? Math.round((latestHealth.healthyServices / latestHealth.totalServices) * 100)
    : 0;

  const huPct = latestHealth?.huScore != null
    ? Math.round(latestHealth.huScore * 100)
    : null;

  const radialData = [
    { name: 'Health', value: healthPct, fill: 'var(--color-success-500)' },
    ...(huPct != null ? [{ name: 'Hᵤ Score', value: huPct, fill: 'var(--color-primary-500)' }] : []),
  ];

  const chartData = healthHistory.map((h) => ({
    time: formatTime(h.ts),
    throughput: h.queryThroughput,
    healthy: h.healthyServices,
  }));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>

      {/* ── Row 1: KPI chips ──────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 'var(--space-3)' }}>
        {[
          {
            label: 'Services healthy',
            value: latestHealth ? `${latestHealth.healthyServices}/${latestHealth.totalServices}` : '–/–',
            color: latestHealth === null ? 'var(--text-secondary)'
              : healthPct >= 80 ? 'var(--color-success-600)'
              : healthPct >= 50 ? 'var(--color-warning-600)'
              : latestHealth.healthyServices > 0 ? 'var(--color-warning-600)'
              : 'var(--color-error-600)',
            subtitle: latestHealth && latestHealth.healthyServices > 0 && healthPct < 50
              ? 'Dev mode — start more services'
              : undefined,
          },
          {
            label: 'Hᵤ score',
            value: huPct != null ? `${huPct}%` : 'N/A',
            color: 'var(--color-primary-600)',
            subtitle: huPct == null ? 'UASR service not running' : undefined,
          },
          {
            label: 'Live alerts',
            value: String(alertCount),
            color: alertCount > 0 ? 'var(--color-warning-600)' : 'var(--text-secondary)',
            subtitle: undefined as string | undefined,
          },
          {
            label: 'SSE stream',
            value: 'Live',
            color: 'var(--color-success-600)',
            subtitle: undefined as string | undefined,
          },
        ].map((kpi) => (
          <div
            key={kpi.label}
            style={{
              padding: 'var(--space-4)',
              borderRadius: 'var(--radius-lg)',
              border: '1px solid var(--border-default)',
              background: 'var(--bg-primary)',
            }}
          >
            <div style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', marginBottom: 'var(--space-1)' }}>
              {kpi.label}
            </div>
            <div style={{ fontSize: 'var(--font-xl)', fontWeight: 'var(--weight-semibold)', color: kpi.color }}>
              {kpi.value}
            </div>
            {kpi.subtitle && (
              <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                {kpi.subtitle}
              </div>
            )}
            <div
              style={{
                width: '6px', height: '6px', borderRadius: '50%',
                background: 'var(--color-success-500)',
                display: 'inline-block', marginTop: 'var(--space-1)',
                animation: 'live-pulse 2s ease-in-out infinite',
              }}
            />
          </div>
        ))}
      </div>

      {/* ── Row 2: Line chart + Radial chart ─────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 'var(--space-5)' }}>

        {/* Service health over time */}
        <Card>
          <CardHeader title="Service health" subtitle="Healthy services over time (live)" />
          <CardBody>
            {chartData.length === 0 ? (
              <ChartSkeleton height={180} />
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={chartData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="healthGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--color-success-500)" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="var(--color-success-500)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
                  <XAxis dataKey="time" tick={{ fontSize: 10 }} tickLine={false} />
                  <YAxis tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--bg-primary)',
                      border: '1px solid var(--border-default)',
                      borderRadius: 'var(--radius-md)',
                      fontSize: '12px',
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="healthy"
                    stroke="var(--color-success-500)"
                    fill="url(#healthGrad)"
                    strokeWidth={2}
                    dot={false}
                    name="Healthy"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </CardBody>
        </Card>

        {/* Radial health/Hᵤ gauge */}
        <Card>
          <CardHeader title="Platform score" subtitle="Health & Hᵤ coefficient" />
          <CardBody>
            {latestHealth === null ? (
              <ChartSkeleton height={180} />
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <RadialBarChart
                  cx="50%"
                  cy="50%"
                  innerRadius="40%"
                  outerRadius="90%"
                  data={radialData}
                  startAngle={90}
                  endAngle={-270}
                >
                  <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
                  <RadialBar
                    dataKey="value"
                    cornerRadius={6}
                    background={{ fill: 'var(--color-neutral-100)' }}
                    label={{ position: 'insideStart', fill: '#fff', fontSize: 11, fontWeight: 600 }}
                  />
                  <Legend
                    iconType="circle"
                    iconSize={8}
                    wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }}
                  />
                  <Tooltip formatter={(v) => `${v}%`} />
                </RadialBarChart>
              </ResponsiveContainer>
            )}
          </CardBody>
        </Card>
      </div>

      {/* ── Row 3: Data quality line chart ───────────────────────── */}
      <Card>
        <CardHeader title="Data quality" subtitle="Service availability % over time" />
        <CardBody>
          {qualityHistory.length === 0 ? (
            <ChartSkeleton height={140} />
          ) : (
            <ResponsiveContainer width="100%" height={150}>
              <LineChart data={qualityHistory.map((q) => ({ ...q, time: formatTime(q.ts) }))} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
                <XAxis dataKey="time" tick={{ fontSize: 10 }} tickLine={false} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} tickLine={false} axisLine={false} unit="%" />
                <Tooltip
                  formatter={(v) => `${v}%`}
                  contentStyle={{
                    background: 'var(--bg-primary)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-md)',
                    fontSize: '12px',
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="quality"
                  stroke="var(--color-primary-500)"
                  strokeWidth={2}
                  dot={false}
                  name="Quality %"
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardBody>
      </Card>

      {/* ── UASR self-healing metrics ───────────────────────────── */}
      <UASRMetricsPanel />
    </div>
  );
}
