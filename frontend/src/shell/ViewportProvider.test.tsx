import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ViewportProvider, useViewport, classForWidth } from './ViewportProvider';

function Probe() {
  const v = useViewport();
  return <span data-testid="v">{`${v.screen}|${v.hasRail}|${v.sidebarMode}`}</span>;
}

function setWidth(w: number) {
  (window as unknown as { innerWidth: number }).innerWidth = w;
  act(() => {
    window.dispatchEvent(new Event('resize'));
  });
}

describe('classForWidth', () => {
  it.each([
    [500, 'compact'],
    [767, 'compact'],
    [768, 'cozy'],
    [1199, 'cozy'],
    [1200, 'standard'],
    [1599, 'standard'],
    [1600, 'wide'],
    [2199, 'wide'],
    [2200, 'ultrawide'],
    [3440, 'ultrawide'],
  ])('width %i -> %s', (w, cls) => {
    expect(classForWidth(w as number)).toBe(cls);
  });
});

describe('useViewport', () => {
  beforeEach(() => {
    (window as unknown as { innerWidth: number }).innerWidth = 1280;
    // Run rAF synchronously so the throttled resize update is observable.
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      cb(0);
      return 0;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  });

  it('exposes screen, hasRail and sidebarMode', () => {
    (window as unknown as { innerWidth: number }).innerWidth = 1700;
    render(<ViewportProvider><Probe /></ViewportProvider>);
    expect(screen.getByTestId('v').textContent).toBe('wide|true|full');
  });

  it('updates on resize', () => {
    render(<ViewportProvider><Probe /></ViewportProvider>);
    expect(screen.getByTestId('v').textContent).toBe('standard|false|full');
    setWidth(2400);
    expect(screen.getByTestId('v').textContent).toBe('ultrawide|true|full');
    setWidth(700);
    expect(screen.getByTestId('v').textContent).toBe('compact|false|drawer');
  });

  it('returns a safe default outside a provider', () => {
    render(<Probe />);
    expect(screen.getByTestId('v').textContent).toBe('standard|false|full');
  });
});
