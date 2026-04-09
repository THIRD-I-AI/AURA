/**
 * ProgressBar
 * ============
 * A flexible progress indicator with multiple variants.
 *
 * Usage:
 *   <ProgressBar value={75} />
 *   <ProgressBar value={0.5} variant="success" animated label="Uploading..." />
 *   <ProgressBar value={90} variant="warning" showPercent />
 *   <ProgressBar indeterminate />
 */
import React from 'react';

type ProgressVariant = 'primary' | 'success' | 'warning' | 'error';

interface ProgressBarProps {
  /** 0–100 or 0.0–1.0 */
  value?: number;
  /** Show a CSS animation for indeterminate/unknown progress */
  indeterminate?: boolean;
  variant?: ProgressVariant;
  /** Striped animated fill */
  animated?: boolean;
  /** Label displayed above the bar */
  label?: string;
  /** Show percentage text at the right end */
  showPercent?: boolean;
  height?: string;
  style?: React.CSSProperties;
}

const TRACK_COLOR = 'var(--color-neutral-200)';
const FILL_COLORS: Record<ProgressVariant, string> = {
  primary: 'var(--color-primary-500)',
  success: 'var(--color-success-500)',
  warning: 'var(--color-warning-500)',
  error: 'var(--color-error-500)',
};

export function ProgressBar({
  value = 0,
  indeterminate = false,
  variant = 'primary',
  animated = false,
  label,
  showPercent = false,
  height = '6px',
  style,
}: ProgressBarProps) {
  const normalized = indeterminate ? 0 : value <= 1 ? value * 100 : value;
  const pct = Math.max(0, Math.min(100, normalized));
  const fillColor = FILL_COLORS[variant];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)', ...style }}>
      {(label || showPercent) && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {label && (
            <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-secondary)', fontWeight: 'var(--weight-medium)' }}>
              {label}
            </span>
          )}
          {showPercent && !indeterminate && (
            <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-secondary)', fontWeight: 'var(--weight-semibold)', marginLeft: 'auto' }}>
              {Math.round(pct)}%
            </span>
          )}
        </div>
      )}

      {/* Track */}
      <div
        role="progressbar"
        aria-valuenow={indeterminate ? undefined : Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        style={{
          width: '100%',
          height,
          borderRadius: 'var(--radius-full)',
          background: TRACK_COLOR,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {/* Fill */}
        <div
          style={{
            height: '100%',
            width: indeterminate ? '40%' : `${pct}%`,
            borderRadius: 'var(--radius-full)',
            background: animated
              ? `repeating-linear-gradient(45deg, ${fillColor}, ${fillColor} 10px, ${fillColor}cc 10px, ${fillColor}cc 20px)`
              : fillColor,
            transition: indeterminate ? 'none' : 'width 0.4s var(--easing-out)',
            animation: indeterminate
              ? 'progress-indeterminate 1.4s ease-in-out infinite'
              : animated
              ? 'progress-stripe 0.7s linear infinite'
              : 'none',
            backgroundSize: animated ? '28px 28px' : 'auto',
            position: 'absolute',
            left: 0,
            top: 0,
          }}
        />
      </div>
    </div>
  );
}

export default ProgressBar;
