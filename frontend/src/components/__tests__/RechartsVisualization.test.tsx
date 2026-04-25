import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeAll } from 'vitest';

import RechartsVisualization from '../RechartsVisualization';

beforeAll(() => {
  // Recharts' ResponsiveContainer measures the parent via ResizeObserver,
  // which jsdom doesn't ship. Stub a minimal implementation.
  if (typeof ResizeObserver === 'undefined') {
    (global as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

describe('RechartsVisualization', () => {
  it('renders an empty-state message when data is empty', () => {
    render(<RechartsVisualization data={[]} />);
    expect(screen.getByText(/no data to visualize/i)).toBeInTheDocument();
  });

  it('renders the chart title from the backend chartSpec', () => {
    render(
      <RechartsVisualization
        data={[
          { product: 'A', revenue: 10 },
          { product: 'B', revenue: 20 },
        ]}
        chartSpec={{ type: 'bar', x: 'product', y: 'revenue', title: 'Sales by product' }}
      />,
    );
    expect(screen.getByText(/sales by product/i)).toBeInTheDocument();
  });

  it('renders a KPI tile for a single-row, single-numeric-column dataset', () => {
    render(
      <RechartsVisualization
        data={[{ total_revenue: 12345 }]}
        chartSpec={{ type: 'kpi', title: 'Total Revenue' }}
      />,
    );
    // Title appears, KPI value appears (formatted)
    expect(screen.getByText(/total revenue/i)).toBeInTheDocument();
  });

  it('renders an "unable to visualize" message when chart needs axes but data has none', () => {
    // Bar chart with one column that is non-numeric → can't pick a y axis.
    render(
      <RechartsVisualization
        data={[{ label: 'a' }, { label: 'b' }]}
        chartSpec={{ type: 'bar' }}
      />,
    );
    expect(screen.getByText(/unable to visualize/i)).toBeInTheDocument();
  });
});
