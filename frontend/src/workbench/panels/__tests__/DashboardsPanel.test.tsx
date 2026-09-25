import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import { dashboardService } from '../../../services/api';
import DashboardsPanel from '../DashboardsPanel';

// BUG-138: tiles were styled as clickable (pointer cursor + hover highlight) but
// nothing opens a dashboard, so the affordance promised an action that didn't exist.
describe('DashboardsPanel', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('lists dashboards without presenting them as clickable', async () => {
    vi.spyOn(dashboardService, 'list').mockResolvedValue([
      { id: 'd1', name: 'Revenue', description: 'weekly', tiles: [{ id: 't1' }, { id: 't2' }] },
    ] as never);
    render(<DashboardsPanel />);

    const tile = await screen.findByTestId('dashboard-tile');
    expect(tile).toHaveTextContent('Revenue');
    expect(tile).toHaveTextContent('2 tiles');
    expect(tile.className).not.toMatch(/cursor-pointer/);
    expect(tile.className).not.toMatch(/hover:border/);
  });
});
