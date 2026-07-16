/**
 * Panel — the AURA Terminal-Authority command-deck frame.
 *
 * This is the primitive every terminal deck (pipeline, audit, analyst, query,
 * …) and the dashboard compose from: a sharp-cornered, dense, bordered surface
 * with an optional mono-uppercase header bar carrying a status glyph and
 * actions. It is hand-authored (not a shadcn primitive) because the terminal
 * frame — header rule, monospace label, status dot — is AURA's own visual
 * signature, not a generic card.
 *
 * All color/spacing/type come from token-bound utilities (bg-card, border,
 * text-text-secondary, font-mono, tracking-wider) so it themes with tokens.css
 * and honors tokens_single_source. Sharp corners + density are the locked
 * visual_direction.
 */
import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/cn'

const panelVariants = cva(
  // base: sharp corners, bordered surface, column flow, clip children to frame
  'flex min-h-0 flex-col overflow-hidden rounded-none border border-border bg-card text-card-foreground',
  {
    variants: {
      tone: {
        /** Standard deck surface. */
        default: '',
        /** Slightly recessed (nested panels, side rails). */
        muted: 'bg-secondary',
        /** Raised (modals, focused deck). */
        elevated: 'bg-popover shadow-lg',
      },
      density: {
        /** Comfortable — dashboard cards. */
        normal: '',
        /** Tight — dense terminal decks (default). */
        tight: '',
      },
    },
    defaultVariants: { tone: 'default', density: 'tight' },
  },
)

export interface PanelProps
  extends React.ComponentProps<'section'>,
    VariantProps<typeof panelVariants> {}

function Panel({ className, tone, density, ...props }: PanelProps) {
  return (
    <section
      data-slot="panel"
      data-density={density ?? 'tight'}
      className={cn(panelVariants({ tone, density }), className)}
      {...props}
    />
  )
}

/**
 * PanelHeader — the deck title bar. Renders a monospace, uppercase, wide-
 * tracked label with an optional leading status glyph and trailing actions,
 * separated from the body by a bottom border rule.
 */
export interface PanelHeaderProps extends Omit<React.ComponentProps<'header'>, 'title'> {
  /** The deck label (rendered mono/uppercase/tracked). */
  title: React.ReactNode
  /** Optional short descriptor to the right of the title, dimmer. */
  hint?: React.ReactNode
  /** Optional leading glyph node (e.g. a StatusGlyph). */
  glyph?: React.ReactNode
  /** Optional trailing actions (buttons, counts). */
  actions?: React.ReactNode
}

function PanelHeader({
  title,
  hint,
  glyph,
  actions,
  className,
  ...props
}: PanelHeaderProps) {
  return (
    <header
      data-slot="panel-header"
      className={cn(
        'flex shrink-0 items-center gap-2 border-b border-border px-3 py-2',
        className,
      )}
      {...props}
    >
      {glyph != null && <span className="flex shrink-0 items-center">{glyph}</span>}
      <span className="truncate font-mono text-2xs font-semibold uppercase tracking-wider text-text-secondary">
        {title}
      </span>
      {hint != null && (
        <span className="truncate font-mono text-2xs text-text-tertiary">{hint}</span>
      )}
      {actions != null && (
        <div className="ml-auto flex shrink-0 items-center gap-1">{actions}</div>
      )}
    </header>
  )
}

/**
 * PanelBody — scrollable content region that fills remaining height. Default
 * padding is dense; pass `flush` to remove it (for full-bleed canvases / DAGs).
 */
function PanelBody({
  className,
  flush = false,
  ...props
}: React.ComponentProps<'div'> & { flush?: boolean }) {
  return (
    <div
      data-slot="panel-body"
      className={cn('min-h-0 flex-1 overflow-auto', flush ? '' : 'p-3', className)}
      {...props}
    />
  )
}

export { Panel, PanelHeader, PanelBody, panelVariants }
