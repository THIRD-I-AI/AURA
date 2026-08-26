/**
 * Sheet — right-side detail drawer. Built on radix-ui's Dialog primitives
 * (the installed `radix-ui` package is the unified export — `Dialog.Root`,
 * `.Portal`, `.Overlay`, `.Content`, `.Title`, `.Description`, `.Close` —
 * not the split `@radix-ui/react-dialog`). A sheet IS a dialog semantically
 * (Escape closes it, focus traps and returns to the trigger, it's modal),
 * just anchored to an edge instead of centered.
 *
 * Real elevation is intentional here: frontend.md's "no drop shadow" rule is
 * for flat panels/cards; floating overlays (command palette, toast in
 * `workbench/Workbench.tsx`) are the documented exception, and a slide-in
 * drawer is the same category.
 *
 * Entrance animation uses the `starting:` variant (CSS `@starting-style`,
 * a native Tailwind v4 variant) rather than pulling in an animate-in/out
 * plugin that isn't installed. Radix unmounts `Dialog.Content` synchronously
 * on close, so there's no matching exit slide.
 * ponytail: no exit animation — Presence-based exit needs `forceMount` plus
 * a real CSS `animation` (not `transition`, which Presence can't detect);
 * add that if a snappier close is ever worth the complexity.
 */
import * as React from 'react'
import { Dialog } from 'radix-ui'
import { X } from 'lucide-react'

import { cn } from '@/lib/cn'

const Sheet = Dialog.Root
const SheetClose = Dialog.Close

function SheetContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof Dialog.Content>) {
  return (
    <Dialog.Portal>
      <Dialog.Overlay
        className="fixed inset-0 z-50 bg-overlay transition-opacity duration-200 ease-out starting:opacity-0"
      />
      <Dialog.Content
        data-slot="sheet-content"
        className={cn(
          'fixed inset-y-0 right-0 z-50 flex h-full w-[480px] max-w-[90vw] flex-col',
          'rounded-none border-l border-border bg-card text-card-foreground',
          'shadow-[0_24px_60px_rgba(0,0,0,.45)] outline-none',
          'transition-transform duration-200 ease-out starting:translate-x-full',
          className,
        )}
        {...props}
      >
        <Dialog.Close
          className={cn(
            'absolute right-3 top-3 inline-flex size-8 items-center justify-center rounded-md',
            'text-text-secondary outline-none transition-colors hover:bg-accent hover:text-accent-foreground',
            'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
          )}
        >
          <X className="size-4" />
          <span className="sr-only">Close</span>
        </Dialog.Close>
        {children}
      </Dialog.Content>
    </Dialog.Portal>
  )
}

function SheetHeader({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="sheet-header"
      className={cn('flex flex-col gap-1 border-b border-border px-4 py-3 pr-12', className)}
      {...props}
    />
  )
}

function SheetTitle({ className, ...props }: React.ComponentProps<typeof Dialog.Title>) {
  return (
    <Dialog.Title
      data-slot="sheet-title"
      className={cn(
        'font-mono text-xs font-semibold uppercase tracking-wider text-text-secondary',
        className,
      )}
      {...props}
    />
  )
}

function SheetDescription({
  className,
  ...props
}: React.ComponentProps<typeof Dialog.Description>) {
  return (
    <Dialog.Description
      data-slot="sheet-description"
      className={cn('font-mono text-2xs text-text-tertiary', className)}
      {...props}
    />
  )
}

/** Scrollable content region — detail views can run long. */
function SheetBody({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="sheet-body"
      className={cn('min-h-0 flex-1 overflow-y-auto p-4', className)}
      {...props}
    />
  )
}

export { Sheet, SheetClose, SheetContent, SheetHeader, SheetTitle, SheetDescription, SheetBody }
