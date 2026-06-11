import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { NAV_ITEMS } from '../nav';
import { Sidebar } from '../Sidebar';

describe('Sidebar', () => {
  // S35a: ids without a NAV_ICON_MAP entry degraded to clipped text
  // fragments ("L…", "C…") in the collapsed rail. Every nav item must
  // render an actual icon.
  it('renders an svg icon for every nav item', () => {
    render(
      <Sidebar items={NAV_ITEMS} activeItem="dashboard" onItemClick={() => {}} />,
    );
    for (const item of NAV_ITEMS) {
      const btn = screen.getByRole('button', { name: item.label });
      expect(btn.querySelector('svg'), `nav item "${item.id}" has no icon`).not.toBeNull();
    }
  });
});
