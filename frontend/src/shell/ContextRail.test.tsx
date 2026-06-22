import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('./railRegistry', () => ({
  RAIL_CONTENT: { dashboard: () => <div>dash rail</div> },
  railTitleFor: (p: string) => (p === 'dashboard' ? 'Overview' : 'Context'),
}));

// DefaultRail (fallback) pulls system health; stub it so the fallback renders.
vi.mock('../hooks/useSystemHealth', () => ({
  useSystemHealth: () => ({ isOnline: true, status: 'healthy' }),
  healthHint: () => 'Gateway healthy',
}));

import { ContextRail } from './ContextRail';

describe('ContextRail', () => {
  beforeEach(() => localStorage.clear());

  it('renders the registry slot for a known page', () => {
    render(<ContextRail page="dashboard" />);
    expect(screen.getByText('dash rail')).toBeInTheDocument();
    expect(screen.getByText('Overview')).toBeInTheDocument();
  });

  it('falls back to DefaultRail content for an unknown page', () => {
    render(<ContextRail page="settings" />);
    expect(screen.getByText(/Quick actions/i)).toBeInTheDocument();
  });

  it('collapses and persists the collapsed state', () => {
    render(<ContextRail page="dashboard" />);
    fireEvent.click(screen.getByRole('button', { name: /collapse/i }));
    expect(localStorage.getItem('aura.rail.collapsed')).toBe('true');
    expect(screen.queryByText('dash rail')).not.toBeInTheDocument();
  });
});
