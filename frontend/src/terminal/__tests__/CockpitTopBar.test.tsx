import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CockpitTopBar } from '../CockpitTopBar';

describe('CockpitTopBar', () => {
  it('applies a layout, opens the palette, and goes back', () => {
    const onApplyLayout = vi.fn();
    const onOpenPalette = vi.fn();
    const onBack = vi.fn();
    render(<CockpitTopBar onApplyLayout={onApplyLayout} onOpenPalette={onOpenPalette} onBack={onBack} />);
    fireEvent.click(screen.getByTestId('layout-auditor'));
    expect(onApplyLayout).toHaveBeenCalledWith('auditor');
    fireEvent.click(screen.getByTestId('open-palette'));
    expect(onOpenPalette).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('back-to-app'));
    expect(onBack).toHaveBeenCalled();
  });
});
