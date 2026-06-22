import { render, screen, act } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { useMediaQuery } from '../useMediaQuery';

function Probe({ q }: { q: string }) {
  const m = useMediaQuery(q);
  return <span data-testid="m">{m ? 'yes' : 'no'}</span>;
}

type Listener = () => void;

function installMatchMedia(initial: boolean) {
  let listener: Listener | null = null;
  const mql = {
    matches: initial,
    media: '',
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: (_: string, cb: Listener) => {
      listener = cb;
    },
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  };
  (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn().mockReturnValue(mql);
  return {
    flip(v: boolean) {
      mql.matches = v;
      listener?.();
    },
  };
}

describe('useMediaQuery', () => {
  beforeEach(() => {
    (window as unknown as { matchMedia: unknown }).matchMedia = undefined;
  });

  it('reports the initial match state', () => {
    installMatchMedia(true);
    render(<Probe q="(max-width: 860px)" />);
    expect(screen.getByTestId('m').textContent).toBe('yes');
  });

  it('reacts when the query starts/stops matching', () => {
    const ctl = installMatchMedia(false);
    render(<Probe q="(max-width: 860px)" />);
    expect(screen.getByTestId('m').textContent).toBe('no');
    act(() => ctl.flip(true));
    expect(screen.getByTestId('m').textContent).toBe('yes');
  });

  it('returns false (desktop path) when matchMedia is unavailable', () => {
    (window as unknown as { matchMedia: unknown }).matchMedia = undefined;
    render(<Probe q="(max-width: 860px)" />);
    expect(screen.getByTestId('m').textContent).toBe('no');
  });
});
