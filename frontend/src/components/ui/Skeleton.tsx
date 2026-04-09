/**
 * Skeleton Loaders
 * =================
 * Shimmer placeholder components for loading states.
 *
 * Usage:
 *   <Skeleton width="100%" height="1rem" />
 *   <KPISkeleton />        — 4-column KPI card strip
 *   <TableSkeleton rows={5} cols={4} />
 *   <ChartSkeleton height={200} />
 *   <CardSkeleton lines={3} />
 */
import React from 'react';

interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  borderRadius?: string;
  style?: React.CSSProperties;
}

export function Skeleton({ width = '100%', height = '1rem', borderRadius = 'var(--radius-sm)', style }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      style={{
        width,
        height,
        borderRadius,
        background: 'var(--color-neutral-200)',
        backgroundImage: 'linear-gradient(90deg, var(--color-neutral-200) 0%, var(--color-neutral-100) 50%, var(--color-neutral-200) 100%)',
        backgroundSize: '200% 100%',
        animation: 'skeleton-shimmer 1.6s ease-in-out infinite',
        ...style,
      }}
    />
  );
}

export function KPISkeleton() {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 'var(--space-4)' }}>
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          style={{
            padding: 'var(--space-5)',
            borderRadius: 'var(--radius-lg)',
            border: '1px solid var(--border-default)',
            background: 'var(--bg-primary)',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-3)',
          }}
        >
          <Skeleton width="60%" height="0.75rem" />
          <Skeleton width="40%" height="1.75rem" borderRadius="var(--radius-md)" />
          <Skeleton width="80%" height="0.75rem" />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      {/* Header */}
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 'var(--space-3)', padding: 'var(--space-3) var(--space-4)' }}>
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} height="0.875rem" width="70%" />
        ))}
      </div>
      {/* Rows */}
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 'var(--space-3)', padding: 'var(--space-3) var(--space-4)', borderTop: '1px solid var(--border-default)' }}>
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} height="0.875rem" width={`${60 + Math.random() * 30}%`} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function ChartSkeleton({ height = 200 }: { height?: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      <Skeleton height={height} borderRadius="var(--radius-md)" />
      <div style={{ display: 'flex', gap: 'var(--space-3)', justifyContent: 'center' }}>
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} width="4rem" height="0.75rem" />
        ))}
      </div>
    </div>
  );
}

export function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)', padding: 'var(--space-5)' }}>
      <Skeleton width="50%" height="1rem" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} width={`${70 + (i % 2) * 20}%`} height="0.875rem" />
      ))}
    </div>
  );
}

export default Skeleton;
