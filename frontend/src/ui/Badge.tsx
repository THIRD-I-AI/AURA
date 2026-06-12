import React from 'react';

export type BadgeStatus = 'verified' | 'pending' | 'warn' | 'danger' | 'neutral';

/** Glyph per status — a11y: color is never the only signal. */
export const STATUS_GLYPHS: Record<BadgeStatus, string> = {
  verified: '✓',
  pending: '◷',
  warn: '⚠',
  danger: '✕',
  neutral: '▪',
};

export const Badge: React.FC<{ status: BadgeStatus; children: React.ReactNode }> = ({ status, children }) => (
  <span className={`ui-badge ui-badge--${status}`}>
    <span aria-hidden="true" className="ui-badge__glyph">{STATUS_GLYPHS[status]}</span>
    {children}
  </span>
);
