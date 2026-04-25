/**
 * RechartsVisualization Component
 *
 * Renders the chart_spec returned by the backend VisualizationAgent.
 * Falls back to local auto-detection only when chartSpec is null/undefined.
 */

import React from 'react';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  ScatterChart,
  Scatter,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import '../styles/design-system.css';

export type ChartType =
  | 'bar'
  | 'stacked_bar'
  | 'line'
  | 'multi_line'
  | 'area'
  | 'pie'
  | 'scatter'
  | 'histogram'
  | 'kpi'
  | 'table'
  | 'auto';

export interface ChartSpec {
  type: ChartType;
  x?: string | null;
  y?: string[] | string | null;
  title?: string;
  reason?: string;
}

interface RechartsVisualizationProps {
  data: Array<Record<string, any>>;
  type?: ChartType;
  title?: string;
  height?: number;
  userQuery?: string;
  chartSpec?: ChartSpec | null;
}

const COLORS = [
  'var(--color-primary-500)',
  'var(--color-success-500)',
  'var(--color-warning-500)',
  'var(--color-error-500)',
  'var(--color-info-500)',
  '#8884d8',
  '#82ca9d',
  '#ffc658',
  '#ff7c7c',
  '#a4a4ff',
];

/** Inspect data shape to pick reasonable x/y when no chartSpec is provided. */
const analyzeDataStructure = (data: Array<Record<string, any>>) => {
  if (!data || data.length === 0) {
    return { xAxis: null as string | null, yAxis: [] as string[], isNumeric: false };
  }

  const firstRow = data[0];
  const columns = Object.keys(firstRow);

  const numericColumns = columns.filter((col) => {
    const values = data.slice(0, 10).map((row) => row[col]);
    return values.every((val) => typeof val === 'number' || (val != null && !isNaN(Number(val))));
  });

  const categoricalColumns = columns.filter((col) => {
    const val = firstRow[col];
    return (
      typeof val === 'string' ||
      val instanceof Date ||
      (typeof val === 'number' && data.length < 50)
    );
  });

  const dateColumn = categoricalColumns.find(
    (col) =>
      col.toLowerCase().includes('date') ||
      col.toLowerCase().includes('time') ||
      col.toLowerCase().includes('month') ||
      col.toLowerCase().includes('year')
  );

  const xAxis = dateColumn || categoricalColumns[0] || columns[0];
  const yAxis = numericColumns.length > 0 ? numericColumns : columns.filter((c) => c !== xAxis);

  return {
    xAxis,
    yAxis: yAxis.slice(0, 3),
    isNumeric: numericColumns.length > 0,
  };
};

/** Local fallback: only used when no backend chart_spec is supplied. */
const autoDetectChartType = (
  data: Array<Record<string, any>>,
  query?: string
): ChartType => {
  if (!query) return 'bar';
  const q = query.toLowerCase();
  if (/(trend|over time|growth|change|progress|timeline)/.test(q)) return 'line';
  if (/(distribution|breakdown|share|percentage|proportion)/.test(q)) return 'pie';
  if (data.length >= 2 && data.length <= 8) return 'pie';
  return 'bar';
};

const tooltipStyle = {
  backgroundColor: 'var(--bg-primary)',
  border: '1px solid var(--border-default)',
  borderRadius: 'var(--radius-md)',
  fontSize: 'var(--font-sm)',
};
const axisStyle = { fontSize: 'var(--font-xs)' };
const legendStyle = { fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' };

/** Bin a numeric series into ~10 buckets for histogram rendering. */
const buildHistogram = (data: Array<Record<string, any>>, col: string) => {
  const values = data
    .map((d) => Number(d[col]))
    .filter((v) => !isNaN(v) && isFinite(v));
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const binCount = Math.min(10, Math.max(4, Math.round(Math.sqrt(values.length))));
  const width = (max - min) / binCount || 1;
  const bins = Array.from({ length: binCount }, (_, i) => ({
    bucket: `${(min + i * width).toFixed(1)}–${(min + (i + 1) * width).toFixed(1)}`,
    count: 0,
  }));
  values.forEach((v) => {
    let idx = Math.floor((v - min) / width);
    if (idx >= binCount) idx = binCount - 1;
    if (idx < 0) idx = 0;
    bins[idx].count += 1;
  });
  return bins;
};

const RechartsVisualization: React.FC<RechartsVisualizationProps> = ({
  data,
  type = 'auto',
  title,
  height = 400,
  userQuery,
  chartSpec,
}) => {
  if (!data || data.length === 0) {
    return (
      <div
        style={{
          padding: 'var(--space-8)',
          textAlign: 'center',
          color: 'var(--text-secondary)',
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-default)',
        }}
      >
        No data to visualize
      </div>
    );
  }

  // Resolve chart type, x, and y. Backend spec wins when present.
  const fallbackStructure = analyzeDataStructure(data);
  let chartType: ChartType;
  let xAxis: string | null;
  let yAxis: string[];
  let resolvedTitle = title || chartSpec?.title;

  if (chartSpec && chartSpec.type) {
    chartType = chartSpec.type;
    xAxis = chartSpec.x ?? fallbackStructure.xAxis;
    const y = chartSpec.y;
    yAxis = Array.isArray(y) ? y : y ? [y] : fallbackStructure.yAxis;
  } else {
    chartType = type === 'auto' ? autoDetectChartType(data, userQuery) : type;
    xAxis = fallbackStructure.xAxis;
    yAxis = fallbackStructure.yAxis;
  }

  if (chartType !== 'kpi' && chartType !== 'table' && chartType !== 'histogram') {
    if (!xAxis || yAxis.length === 0) {
      return (
        <div style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--text-secondary)' }}>
          Unable to visualize this data structure
        </div>
      );
    }
  }

  const renderChart = () => {
    switch (chartType) {
      case 'line':
      case 'multi_line':
        return (
          <ResponsiveContainer width="100%" height={height}>
            <LineChart data={data} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
              <XAxis dataKey={xAxis!} stroke="var(--text-secondary)" style={axisStyle} />
              <YAxis stroke="var(--text-secondary)" style={axisStyle} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={legendStyle} />
              {yAxis.map((col, idx) => (
                <Line
                  key={col}
                  type="monotone"
                  dataKey={col}
                  stroke={COLORS[idx % COLORS.length]}
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  activeDot={{ r: 5 }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        );

      case 'area':
        return (
          <ResponsiveContainer width="100%" height={height}>
            <AreaChart data={data} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
              <XAxis dataKey={xAxis!} stroke="var(--text-secondary)" style={axisStyle} />
              <YAxis stroke="var(--text-secondary)" style={axisStyle} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={legendStyle} />
              {yAxis.map((col, idx) => (
                <Area
                  key={col}
                  type="monotone"
                  dataKey={col}
                  stroke={COLORS[idx % COLORS.length]}
                  fill={COLORS[idx % COLORS.length]}
                  fillOpacity={0.3}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        );

      case 'scatter': {
        const xCol = xAxis!;
        const yCol = yAxis[0];
        const points = data
          .map((d) => ({ [xCol]: Number(d[xCol]), [yCol]: Number(d[yCol]) }))
          .filter((d) => !isNaN(d[xCol]) && !isNaN(d[yCol]));
        return (
          <ResponsiveContainer width="100%" height={height}>
            <ScatterChart margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
              <XAxis type="number" dataKey={xCol} name={xCol} stroke="var(--text-secondary)" style={axisStyle} />
              <YAxis type="number" dataKey={yCol} name={yCol} stroke="var(--text-secondary)" style={axisStyle} />
              <ZAxis range={[60, 60]} />
              <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={tooltipStyle} />
              <Legend wrapperStyle={legendStyle} />
              <Scatter name={`${yCol} vs ${xCol}`} data={points} fill={COLORS[0]} />
            </ScatterChart>
          </ResponsiveContainer>
        );
      }

      case 'histogram': {
        const col = yAxis[0] || xAxis || Object.keys(data[0])[0];
        const bins = buildHistogram(data, col);
        return (
          <ResponsiveContainer width="100%" height={height}>
            <BarChart data={bins} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
              <XAxis dataKey="bucket" stroke="var(--text-secondary)" style={axisStyle} />
              <YAxis stroke="var(--text-secondary)" style={axisStyle} />
              <Tooltip contentStyle={tooltipStyle} />
              <Bar dataKey="count" fill={COLORS[0]} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        );
      }

      case 'pie': {
        const yCol = yAxis[0];
        const pieData = data.map((item, idx) => ({
          name: String(item[xAxis!] ?? `Item ${idx + 1}`),
          value: Number(item[yCol] ?? 1),
        }));
        return (
          <ResponsiveContainer width="100%" height={height}>
            <PieChart>
              <Pie
                data={pieData}
                cx="50%"
                cy="50%"
                labelLine={false}
                label={({ name, percent }) => `${name}: ${((percent ?? 0) * 100).toFixed(0)}%`}
                outerRadius={Math.min(120, height / 3)}
                fill="var(--color-primary-500)"
                dataKey="value"
              >
                {pieData.map((_entry, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={legendStyle} />
            </PieChart>
          </ResponsiveContainer>
        );
      }

      case 'kpi': {
        const yCol = yAxis[0] || Object.keys(data[0])[0];
        const value = data[0]?.[yCol];
        return (
          <div
            style={{
              height,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 'var(--space-2)',
            }}
          >
            <div style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              {yCol}
            </div>
            <div style={{ fontSize: 48, fontWeight: 700, color: 'var(--text-primary)' }}>
              {typeof value === 'number' ? value.toLocaleString() : String(value ?? '—')}
            </div>
          </div>
        );
      }

      case 'table':
        // Caller already shows a table; render a no-op note here.
        return (
          <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 'var(--font-sm)' }}>
            See the data table below.
          </div>
        );

      case 'stacked_bar':
      case 'bar':
      default:
        return (
          <ResponsiveContainer width="100%" height={height}>
            <BarChart data={data} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
              <XAxis dataKey={xAxis!} stroke="var(--text-secondary)" style={axisStyle} />
              <YAxis stroke="var(--text-secondary)" style={axisStyle} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={legendStyle} />
              {yAxis.map((col, idx) => (
                <Bar
                  key={col}
                  dataKey={col}
                  fill={COLORS[idx % COLORS.length]}
                  stackId={chartType === 'stacked_bar' ? 'a' : undefined}
                  radius={[4, 4, 0, 0]}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        );
    }
  };

  return (
    <div
      style={{
        padding: 'var(--space-6)',
        backgroundColor: 'var(--bg-primary)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border-default)',
        marginBottom: 'var(--space-4)',
      }}
    >
      {resolvedTitle && (
        <h3
          style={{
            margin: '0 0 var(--space-4) 0',
            fontSize: 'var(--font-lg)',
            fontWeight: 'var(--weight-semibold)',
            color: 'var(--text-primary)',
          }}
        >
          {resolvedTitle}
        </h3>
      )}
      {renderChart()}
      <div
        style={{
          marginTop: 'var(--space-3)',
          fontSize: 'var(--font-xs)',
          color: 'var(--text-tertiary)',
          textAlign: 'center',
        }}
        title={chartSpec?.reason}
      >
        Showing {data.length} record{data.length !== 1 ? 's' : ''} • {chartType.toUpperCase()} chart
      </div>
    </div>
  );
};

export default RechartsVisualization;
