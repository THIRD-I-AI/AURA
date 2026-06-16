import React from 'react';

import { STATUS_GLYPHS, type BadgeStatus } from './status';

export const Badge: React.FC<{ status: BadgeStatus; children: React.ReactNode }> = ({ status, children }) => (
  <span className={`ui-badge ui-badge--${status}`}>
    <span aria-hidden="true" className="ui-badge__glyph">{STATUS_GLYPHS[status]}</span>
    {children}
  </span>
);
