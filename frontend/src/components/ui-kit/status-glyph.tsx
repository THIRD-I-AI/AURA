/**
 * StatusGlyph — the honest single-character state marker used across decks.
 *
 * Encodes the status-honesty rules locked for this redesign:
 *   • 'ok'        ● green   — healthy / verified / live
 *   • 'warn'      ● amber   — degraded / half-open
 *   • 'error'     ● red     — failed / open / broken
 *   • 'awaiting'  ◌ dim     — MONITORED but no data yet (honest unknown; NOT ok)
 *   • 'unmonitored' · faint — infra we do not observe (distinct from awaiting)
 *   • 'verifying' ◍ pulse   — verification in flight
 *
 * The 'awaiting' vs 'unmonitored' distinction is deliberate (status_unknown_state):
 * ◌ says "we're watching, nothing has arrived", · says "we're not watching".
 * Neither is ever rendered as green/ok. Colors come from token-bound utilities.
 */
import { cn } from '@/lib/cn'

export type Status =
  | 'ok'
  | 'warn'
  | 'error'
  | 'awaiting'
  | 'unmonitored'
  | 'verifying'

const GLYPH: Record<Status, string> = {
  ok: '●',
  warn: '●',
  error: '●',
  awaiting: '◌',
  unmonitored: '·',
  verifying: '◍',
}

const COLOR: Record<Status, string> = {
  ok: 'text-[var(--cb-closed)]',
  warn: 'text-[var(--cb-half-open)]',
  error: 'text-[var(--cb-open)]',
  awaiting: 'text-text-tertiary',
  unmonitored: 'text-[var(--text-disabled)]',
  verifying: 'text-text-secondary',
}

const LABEL: Record<Status, string> = {
  ok: 'healthy',
  warn: 'degraded',
  error: 'failed',
  awaiting: 'monitored — awaiting data',
  unmonitored: 'unmonitored',
  verifying: 'verifying',
}

export interface StatusGlyphProps {
  status: Status
  /** Accessible label override; defaults to the status meaning. */
  label?: string
  className?: string
}

export function StatusGlyph({ status, label, className }: StatusGlyphProps) {
  return (
    <span
      role="img"
      aria-label={label ?? LABEL[status]}
      title={label ?? LABEL[status]}
      className={cn(
        'inline-block select-none font-mono text-2xs leading-none',
        COLOR[status],
        status === 'verifying' && 'motion-safe:animate-pulse',
        className,
      )}
    >
      {GLYPH[status]}
    </span>
  )
}
