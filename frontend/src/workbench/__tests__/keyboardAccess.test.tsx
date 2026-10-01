import { createRef, useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../auth/UserMenu', () => ({ UserMenu: () => null }));
vi.mock('../../services/api', () => ({ getCurrentWorkspaceId: () => 'default' }));

import { CommandPalette, type Command } from '../CommandPalette';
import { WorkbenchTopbar } from '../WorkbenchTopbar';

// BUG-256: these controls were div onClick with no keyboard path.
describe('BUG-256: Workbench chrome is keyboard operable', () => {
  function Palette({ commands }: { commands: Command[] }) {
    const [q, setQ] = useState('');
    return (
      <CommandPalette paletteOpen onClose={() => undefined} paletteQ={q} setPaletteQ={setQ}
        paletteInput={createRef<HTMLInputElement>()} commands={commands} />
    );
  }

  it('arrow keys move through palette commands and Enter runs the highlighted one', () => {
    const runs = [vi.fn(), vi.fn(), vi.fn()];
    render(<Palette commands={runs.map((run, i) => ({ title: `cmd ${i}`, hint: 'go', run }))} />);
    const input = screen.getByRole('combobox');
    expect(screen.getByRole('option', { name: /cmd 0/ })).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' }); // clamps at the last item
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(screen.getByRole('option', { name: /cmd 1/ })).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(input, { key: 'Enter' });
    expect(runs[1]).toHaveBeenCalledTimes(1);
    expect(runs[0]).not.toHaveBeenCalled();
  });

  it('the topbar hamburger and search launcher respond to Enter and Space', () => {
    const onToggleNav = vi.fn();
    const onOpenPalette = vi.fn();
    render(<WorkbenchTopbar onToggleNav={onToggleNav} gatewayUp onOpenPalette={onOpenPalette} />);

    const burger = screen.getByRole('button', { name: 'Toggle navigation' });
    expect(burger).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(burger, { key: 'Enter' });
    expect(onToggleNav).toHaveBeenCalledTimes(1);

    const launcher = screen.getByRole('button', { name: /search, ask, or run a command/i });
    fireEvent.keyDown(launcher, { key: ' ' });
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
  });
});
