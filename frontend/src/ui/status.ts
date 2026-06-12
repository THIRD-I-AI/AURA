export type BadgeStatus = 'verified' | 'pending' | 'warn' | 'danger' | 'neutral';

/** Glyph per status — a11y: color is never the only signal. Lives outside
 * Badge.tsx so tests/components can import it without tripping
 * react-refresh/only-export-components. */
export const STATUS_GLYPHS: Record<BadgeStatus, string> = {
  verified: '✓',
  pending: '◷',
  warn: '⚠',
  danger: '✕',
  neutral: '▪',
};
