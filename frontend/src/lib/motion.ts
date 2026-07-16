/**
 * Motion vocabulary — Terminal-Authority redesign (Phase 2).
 *
 * A small, NAMED set of transitions and variants so animation across the
 * redesign is consistent and legible rather than ad-hoc. Import these instead
 * of inlining `transition={{ ... }}` at call sites.
 *
 * Design intent (matches visual_direction "pure Terminal-authority"):
 *   • Fast, crisp, non-bouncy by default — a command deck, not a marketing page.
 *   • Motion is FUNCTIONAL: it signals state change (panel mounted, status
 *     updated, deck switched), it does not decorate.
 *   • Durations are short (120–260ms). Easings come from design-system.css
 *     (--ease-out / --ease-in-out / --ease-spring) so the timing language is
 *     single-sourced with the CSS transitions already in the app.
 *
 * Reduced motion: `prefersReducedMotion()` reads the OS setting at call time.
 * Wrap variant selection with `maybe(...)` so animation degrades to an instant
 * state change (no transform/opacity tweening) when the user asks for it — the
 * CSS reset in design-system.css covers CSS transitions; this covers JS/Motion.
 */
import type { Transition, Variants } from 'motion/react'

/* ── Easing tuples (mirror design-system.css --ease-*) ──────────────────────
   Motion wants numeric cubic-bezier tuples, not var() strings, so these are
   kept bit-identical to the CSS tokens by value. If a CSS ease changes, change
   it here too (the one place the single-source rule can't reach into JS). */
export const EASE = {
  out: [0, 0, 0.2, 1],
  inOut: [0.4, 0, 0.2, 1],
  spring: [0.34, 1.56, 0.64, 1],
  bounce: [0.25, 1.6, 0.5, 1],
} as const

/* ── Named transitions ──────────────────────────────────────────────────── */
export const DUR = { fast: 0.12, base: 0.18, slow: 0.26 } as const

export const transitions = {
  /** Default UI transition — crisp ease-out, no overshoot. */
  base: { duration: DUR.base, ease: EASE.out } satisfies Transition,
  /** Snappy micro-interaction (hover, press, glyph flip). */
  fast: { duration: DUR.fast, ease: EASE.out } satisfies Transition,
  /** Larger surface entrance (panel/deck mount). */
  panel: { duration: DUR.slow, ease: EASE.out } satisfies Transition,
  /** Spring for elements that should feel physical (rare — nav pill, focus ring). */
  spring: { type: 'spring', stiffness: 520, damping: 34, mass: 0.7 } satisfies Transition,
} as const

/* ── Named variant sets (use with <motion.div variants=… initial/animate/exit) ── */

/** Fade + short upward rise — the default panel/card entrance. */
export const fadeRise: Variants = {
  hidden: { opacity: 0, y: 6 },
  visible: { opacity: 1, y: 0, transition: transitions.panel },
  exit: { opacity: 0, y: 4, transition: transitions.fast },
}

/** Pure fade — for content swaps where movement would distract. */
export const fade: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: transitions.base },
  exit: { opacity: 0, transition: transitions.fast },
}

/** Deck/tab switch — slide a hair from the right, crisp. */
export const deckSwitch: Variants = {
  hidden: { opacity: 0, x: 8 },
  visible: { opacity: 1, x: 0, transition: transitions.panel },
  exit: { opacity: 0, x: -8, transition: transitions.fast },
}

/** Stagger container — reveal children (log rows, list items) in sequence. */
export const staggerContainer: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.035, delayChildren: 0.02 } },
}

/** Stagger child — pair with staggerContainer. */
export const staggerItem: Variants = {
  hidden: { opacity: 0, y: 4 },
  visible: { opacity: 1, y: 0, transition: transitions.fast },
}

/* ── Reduced-motion helpers ─────────────────────────────────────────────── */

/** True when the OS/browser asks for reduced motion (SSR-safe, live at call). */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * Collapse a variant set to instant state changes when reduced motion is on:
 * keeps the same visual end-states (opacity/position) but strips transforms and
 * zeroes duration, so the element snaps rather than tweens. Returns the original
 * variants otherwise.
 */
export function maybe(variants: Variants): Variants {
  if (!prefersReducedMotion()) return variants
  const instant: Transition = { duration: 0 }
  return {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: instant },
    exit: { opacity: 0, transition: instant },
  }
}
