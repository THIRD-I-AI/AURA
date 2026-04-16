/**
 * RechartsVisualization Component
 * Modern data visualization with automatic chart type detection
 * Uses Recharts library for enterprise-grade charts
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
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import '../styles/design-system.css';

export type ChartType = 'bar' | 'line' | 'pie' | 'auto';

interface RechartsVisualizationProps {
  data: Array<Record<string, any>>;
  type?: ChartType;
  title?: string;
  height?: number;
  userQuery?: string; // For auto-detection
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

/**
 * Analyze data structure to determine best columns for visualization
 */
const analyzeDataStructure = (data: Array<Record<string, any>>) => {
  if (!data || data.length === 0) {
    return { xAxis: null, yAxis: [], isNumeric: false };
  }

  const firstRow = data[0];
  const columns = Object.keys(firstRow);

  // Find numeric columns
  const numericColumns = columns.filter((col) => {
    const values = data.slice(0, 10).map((row) => row[col]);
    return values.every((val) => typeof val === 'number' || !isNaN(Number(val)));
  });

  // Find categorical/date columns (for X-axis)
  const categoricalColumns = columns.filter((col) => {
    const val = firstRow[col];
    return (
      typeof val === 'string' ||
      val instanceof Date ||
      (typeof val === 'number' && data.length < 50)
    );
  });

  // Prefer date-like columns for X-axis
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
    yAxis: yAxis.slice(0, 3), // Max 3 series
    isNumeric: numericColumns.length > 0,
  };
};

/**
 * Determine chart type based on query intent or data structure
 */
const autoDetectChartType = (
  data: Array<Record<string, any>>,
  query?: string
): ChartType => {
  if (!query) return 'bar';

  const lowerQuery = query.toLowerCase();

  // Trend/time-series indicators
  if (
    lowerQuery.includes('trend') ||
    lowerQuery.includes('over time') ||
    lowerQuery.includes('growth') ||
    lowerQuery.includes('change') ||
    lowerQuery.includes('progress') ||
    lowerQuery.includes('timeline')
  ) {
    return 'line';
  }

  // Distribution indicators
  if (
    lowerQuery.includes('distribution') ||
    lowerQuery.includes('breakdown') ||
    lowerQuery.includes('share') ||
    lowerQuery.includes('percentage') ||
    lowerQuery.includes('proportion')
  ) {
    return 'pie';
  }

  // Pie chart if only 2-8 categories
  if (data.length >= 2 && data.length <= 8) {
    return 'pie';
  }

  return 'bar';
};

const RechartsVisualization: React.FC<RechartsVisualizationProps> = ({
  data,
  type = 'auto',
  title,
  height = 400,
  userQuery,
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

  const structure = analyzeDataStructure(data);
  const chartType = type === 'auto' ? autoDetectChartType(data, userQuery) : type;

  if (!structure.xAxis || structure.yAxis.length === 0) {
    return (
      <div
        style={{
          padding: 'var(--space-8)',
          textAlign: 'center',
          color: 'var(--text-secondary)',
        }}
      >
        Unable to visualize this data structure
      </div>
    );
  }

  const renderChart = () => {
    switch (chartType) {
      case 'line':
        return (
          <ResponsiveContainer width="100%" height={height}>
            <LineChart data={data} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
              <XAxis
                dataKey={structure.xAxis}
                stroke="var(--text-secondary)"
                style={{ fontSize: 'var(--font-xs)' }}
              />
              <YAxis
                stroke="var(--text-secondary)"
                style={{ fontSize: 'var(--font-xs)' }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--bg-primary)',
                  border: '1px solid var(--border-default)',
                  borderRadius: 'var(--radius-md)',
                  fontSize: 'var(--font-sm)',
                }}
              />
              <Legend
                wrapperStyle={{
                  fontSize: 'var(--font-sm)',
                  color: 'var(--text-secondary)',
                }}
              />
              {structure.yAxis.map((col, idx) => (
                <Line
                  key={col}
                  type="monotone"
                  dataKey={col}
                  stroke={COLORS[idx % COLORS.length]}
                  strokeWidth={2}
                  dot={{ r: 4 }}
                  activeDot={{ r: 6 }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        );

      case 'pie': {
        const pieData = data.map((item, idx) => ({
          name: String(item[structure.xAxis] || `Item ${idx + 1}`),
          value: Number(item[structure.yAxis[0]] || 1),
        }));

        return (
          <ResponsiveContainer width="100%" height={height}>
            <PieChart>
              <Pie
                data={pieData}
                cx="50%"
                cy="50%"
                labelLine={false}
                label={({ name, percent }) =>
                  `${name}: ${((percent ?? 0) * 100).toFixed(0)}%`
                }
                outerRadius={120}
                fill="var(--color-primary-500)"
                dataKey="value"
              >
                {pieData.map((_entry, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--bg-primary)',
                  border: '1px solid var(--border-default)',
                  borderRadius: 'var(--radius-md)',
                  fontSize: 'var(--font-sm)',
                }}
              />
              <Legend
                wrapperStyle={{
                  fontSize: 'var(--font-sm)',
                  color: 'var(--text-secondary)',
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        );
      }

      case 'bar':
      default:
        return (
          <ResponsiveContainer width="100%" height={height}>
            <BarChart data={data} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-default)" />
              <XAxis
                dataKey={structure.xAxis}
                stroke="var(--text-secondary)"
                style={{ fontSize: 'var(--font-xs)' }}
              />
              <YAxis
                stroke="var(--text-secondary)"
                style={{ fontSize: 'var(--font-xs)' }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--bg-primary)',
                  border: '1px solid var(--border-default)',
                  borderRadius: 'var(--radius-md)',
                  fontSize: 'var(--font-sm)',
                }}
              />
              <Legend
                wrapperStyle={{
                  fontSize: 'var(--font-sm)',
                  color: 'var(--text-secondary)',
                }}
              />
              {structure.yAxis.map((col, idx) => (
                <Bar
                  key={col}
                  dataKey={col}
                  fill={COLORS[idx % COLORS.length]}
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
      {title && (
        <h3
          style={{
            margin: '0 0 var(--space-4) 0',
            fontSize: 'var(--font-lg)',
            fontWeight: 'var(--weight-semibold)',
            color: 'var(--text-primary)',
          }}
        >
          {title}
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
      >
        Showing {data.length} record{data.length !== 1 ? 's' : ''} • {chartType.toUpperCase()} chart
      </div>
    </div>
  );
};

export default RechartsVisualization;
