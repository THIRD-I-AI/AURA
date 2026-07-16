/**
 * EmptyState — the designed empty / awaiting / error surface.
 *
 * Replaces raw failure strings ("Couldn't load scenarios") and blank panels
 * with an honest, composed state. Three intents, each visually distinct so the
 * operator can tell "nothing has happened yet" from "something broke":
 *
 *   • 'awaiting' — monitored, no data yet. Neutral, calm. Uses ◌.
 *   • 'empty'    — no results for a query/filter. Neutral.
 *   • 'error'    — a load/fetch failed. Red accent + optional Retry action.
 *
 * Colors/spacing/type are token-bound utilities. Sharp, dense, mono label —
 * consistent with the deck frame it sits inside.
 */
import * as React from 'react'

import { cn } from '@/lib/cn'
import { StatusGlyph } from '@/components/ui-kit/status-glyph'

export type EmptyStateIntent = 'awaiting' | 'empty' | 'error'

export interface EmptyStateProps extends Omit<React.ComponentProps<'div'>, 'title'> {
  intent?: EmptyStateIntent
  /** Short mono label, e.g. "AWAITING DATA" / "NO FINDINGS" / "LOAD FAILED". */
  title: React.ReactNode
  /** One-line explanation in prose. */
  description?: React.ReactNode
  /** Optional custom icon; defaults to a StatusGlyph matched to intent. */
  icon?: React.ReactNode
  /** Optional action (e.g. a Retry button). */
  action?: React.ReactNode
}

const INTENT_GLYPH = {
  awaiting: 'awaiting',
  empty: 'unmonitored',
  error: 'error',
} as const

export function EmptyState({
  intent = 'empty',
  title,
  description,
  icon,
  action,
  className,
  ...props
}: EmptyStateProps) {
  return (
    <div
      data-slot="empty-state"
      data-intent={intent}
      role={intent === 'error' ? 'alert' : 'status'}
      className={cn(
        'flex h-full min-h-0 w-full flex-col items-center justify-center gap-3 p-6 text-center',
        className,
      )}
      {...props}
    >
      <span className="text-lg leading-none">
        {icon ?? <StatusGlyph status={INTENT_GLYPH[intent]} />}
      </span>
      <div className="flex flex-col items-center gap-1">
        <span
          className={cn(
            'font-mono text-xs font-semibold uppercase tracking-wider',
            intent === 'error' ? 'text-[var(--cb-open)]' : 'text-text-secondary',
          )}
        >
          {title}
        </span>
        {description != null && (
          <p className="max-w-[42ch] text-sm leading-snug text-text-tertiary">
            {description}
          </p>
        )}
      </div>
      {action != null && <div className="mt-1">{action}</div>}
    </div>
  )
}
