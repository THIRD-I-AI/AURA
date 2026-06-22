import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../shell/ViewportProvider', async (orig) => {
  const real = (await orig()) as Record<string, unknown>;
  return {
    ...real,
    useViewport: () => ({
      width: 1900, height: 900, size: 'wide', hasRail: true,
      sidebarMode: 'full', atLeast: () => true,
    }),
  };
});
vi.mock('./Sidebar', () => ({ default: () => <div data-testid="sb" /> }));
vi.mock('./Header', () => ({ default: () => <div data-testid="hd" /> }));
vi.mock('../../shell/ContextRail', () => ({
  ContextRail: ({ page }: { page: string }) => <div data-testid="rail">{page}</div>,
}));

import AppLayout from './AppLayout';

describe('AppLayout rail integration', () => {
  it('mounts the ContextRail and sets data-rail/data-viewport when hasRail', () => {
    render(
      <MemoryRouter>
        <AppLayout currentPage="dashboard" onPageChange={() => {}}>
          <div>body</div>
        </AppLayout>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('rail')).toHaveTextContent('dashboard');
    const shell = document.querySelector('.app-shell');
    expect(shell?.getAttribute('data-rail')).toBe('true');
    expect(shell?.getAttribute('data-viewport')).toBe('wide');
  });
});
