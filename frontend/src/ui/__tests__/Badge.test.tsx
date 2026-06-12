import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Badge } from '../Badge';
import { STATUS_GLYPHS } from '../status';

describe('ui/Badge', () => {
  it('always pairs color with a glyph — color is never the only signal', () => {
    for (const status of ['verified', 'pending', 'warn', 'danger', 'neutral'] as const) {
      const { unmount } = render(<Badge status={status}>{status}</Badge>);
      const badge = screen.getByText(status).closest('.ui-badge')!;
      expect(badge.className).toContain(`ui-badge--${status}`);
      expect(badge.textContent).toContain(STATUS_GLYPHS[status]);
      unmount();
    }
  });
});
