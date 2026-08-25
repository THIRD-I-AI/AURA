/**
 * Skeleton — a structural loading placeholder shaped like the content it
 * stands in for, not a generic "Loading…" string.
 *
 * `EmptyState intent="awaiting"` stays correct for a whole panel that's
 * waiting on its first fetch (see .claude/rules/frontend.md — "Loading
 * states"). Reach for `Skeleton` instead when the surrounding layout is
 * already known (a stat tile, a table row) and showing its real shape
 * during the wait is worth the extra markup.
 *
 * Sharp corners (rounded-none) — matches the house Panel/Card frame, never
 * a rounded pill. Composes bars/blocks via className (`h-4 w-24`, `h-3
 * w-full`, …); this primitive owns only the shimmer, not any shape.
 */
import * as React from 'react'

import { cn } from '@/lib/cn'

function Skeleton({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn('animate-pulse rounded-none bg-raised', className)}
      {...props}
    />
  )
}

export { Skeleton }
