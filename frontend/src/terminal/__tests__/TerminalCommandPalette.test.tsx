import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TerminalCommandPalette } from '../TerminalCommandPalette';
import { buildTerminalCommands } from '../commands';

describe('TerminalCommandPalette', () => {
  it('fuzzy-filters and runs the top command on Enter', () => {
    const openPanel = vi.fn();
    const commands = buildTerminalCommands({ openPanel, applyLayout: vi.fn(), resetLayout: vi.fn(), back: vi.fn() });
    render(<TerminalCommandPalette open={true} onClose={() => {}} commands={commands} />);
    fireEvent.change(screen.getByTestId('palette-input'), { target: { value: 'findings' } });
    fireEvent.keyDown(screen.getByTestId('palette-input'), { key: 'Enter' });
    expect(openPanel).toHaveBeenCalledWith('findings');
  });

  it('renders nothing when closed', () => {
    const commands = buildTerminalCommands({ openPanel: vi.fn(), applyLayout: vi.fn(), resetLayout: vi.fn(), back: vi.fn() });
    const { container } = render(<TerminalCommandPalette open={false} onClose={() => {}} commands={commands} />);
    expect(container).toBeEmptyDOMElement();
  });
});
