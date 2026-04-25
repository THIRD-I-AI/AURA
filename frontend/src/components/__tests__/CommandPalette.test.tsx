import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  savedQueryService: {
    list: vi.fn().mockResolvedValue([
      { id: 'sq_1', name: 'Daily revenue', sql: 'SELECT 1', prompt: 'How much money?', starred: true, created_at: '', updated_at: '' },
      { id: 'sq_2', name: 'Top customers', sql: 'SELECT 2', prompt: null, starred: false, created_at: '', updated_at: '' },
    ]),
  },
}));

import CommandPalette from '../CommandPalette';

describe('CommandPalette', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });
  afterEach(() => {
    sessionStorage.clear();
  });

  it('is hidden by default and opens on Ctrl+K', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<CommandPalette onNavigate={onNavigate} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.keyboard('{Control>}k{/Control}');
    expect(await screen.findByRole('dialog', { name: /command palette/i })).toBeInTheDocument();
  });

  it('navigates to a page when an option is selected with Enter', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<CommandPalette onNavigate={onNavigate} />);
    await user.keyboard('{Control>}k{/Control}');

    const input = await screen.findByLabelText(/command palette search/i);
    await user.type(input, 'pipelines');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('pipelines'));
  });

  it('opens a saved query in chat and seeds the handoff in sessionStorage', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<CommandPalette onNavigate={onNavigate} />);
    await user.keyboard('{Control>}k{/Control}');

    const input = await screen.findByLabelText(/command palette search/i);
    // Wait for saved queries to load + render
    await screen.findByText(/Open: Daily revenue/i);

    await user.clear(input);
    await user.type(input, 'daily');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('chat'));
    const handoff = sessionStorage.getItem('aura.library.openQuery');
    expect(handoff).not.toBeNull();
    expect(JSON.parse(handoff!)).toMatchObject({ name: 'Daily revenue', sql: 'SELECT 1' });
  });

  it('closes on Escape', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<CommandPalette onNavigate={onNavigate} />);
    await user.keyboard('{Control>}k{/Control}');
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
