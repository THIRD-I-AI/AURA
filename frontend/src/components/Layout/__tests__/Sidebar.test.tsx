import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { NAV_ITEMS } from '../nav';
import { Sidebar } from '../Sidebar';

describe('Sidebar', () => {
  // S35a: ids without a NAV_ICON_MAP entry degraded to clipped text
  // fragments ("L…", "C…") in the collapsed rail. Every nav item must
  // render an actual icon.
  // Timeout raised from the 5s default: the loop below runs one name-filtered
  // getByRole per nav item, and each of those rebuilds the whole accessibility
  // tree — ~25 full tree computations. That is slow, not broken, so it passes
  // alone and under `--no-file-parallelism` but blew 5s whenever vitest workers
  // competed for CPU. Calibrating the budget, not weakening the assertion:
  // every nav item must still render a real <svg>.
  it('renders an svg icon for every nav item', () => {
    render(
      <Sidebar items={NAV_ITEMS} activeItem="dashboard" onItemClick={() => {}} />,
    );
    for (const item of NAV_ITEMS) {
      const btn = screen.getByRole('button', { name: item.label });
      expect(btn.querySelector('svg'), `nav item "${item.id}" has no icon`).not.toBeNull();
    }
  }, 20_000);

  it('groups nav items under the six auditor-workbench sections', () => {
    render(<Sidebar items={NAV_ITEMS} activeItem="dashboard" onItemClick={() => {}} />);
    for (const heading of ['Engagements', 'Evidence & Data', 'Findings', 'Certificates', 'Monitoring', 'Admin']) {
      expect(screen.getByText(heading)).toBeInTheDocument();
    }
  });
});
