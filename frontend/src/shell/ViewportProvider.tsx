import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export type ScreenClass = 'compact' | 'cozy' | 'standard' | 'wide' | 'ultrawide';

export const BREAKPOINTS = { cozy: 768, standard: 1200, wide: 1600, ultrawide: 2200 } as const;

const ORDER: ScreenClass[] = ['compact', 'cozy', 'standard', 'wide', 'ultrawide'];

// eslint-disable-next-line react-refresh/only-export-components
export function classForWidth(w: number): ScreenClass {
  if (w >= BREAKPOINTS.ultrawide) return 'ultrawide';
  if (w >= BREAKPOINTS.wide) return 'wide';
  if (w >= BREAKPOINTS.standard) return 'standard';
  if (w >= BREAKPOINTS.cozy) return 'cozy';
  return 'compact';
}

export interface Viewport {
  width: number;
  height: number;
  screen: ScreenClass;
  hasRail: boolean;
  sidebarMode: 'drawer' | 'rail' | 'full';
  atLeast: (c: ScreenClass) => boolean;
}

function deriveViewport(width: number, height: number): Viewport {
  const screen = classForWidth(width);
  const sidebarMode = screen === 'compact' ? 'drawer' : screen === 'cozy' ? 'rail' : 'full';
  return {
    width,
    height,
    screen,
    hasRail: screen === 'wide' || screen === 'ultrawide',
    sidebarMode,
    atLeast: (c) => ORDER.indexOf(screen) >= ORDER.indexOf(c),
  };
}

// Safe default: outside a provider, or with no window (SSR/tests), report a
// neutral desktop. Returning a default — rather than throwing — keeps isolated
// component tests green without each one wrapping in a provider.
const DEFAULT: Viewport = deriveViewport(1280, 800);

const ViewportContext = createContext<Viewport>(DEFAULT);

export function ViewportProvider({ children }: { children: ReactNode }) {
  const [size, setSize] = useState(() =>
    typeof window === 'undefined'
      ? { w: 1280, h: 800 }
      : { w: window.innerWidth, h: window.innerHeight },
  );

  useEffect(() => {
    if (typeof window === 'undefined') return;
    let frame = 0;
    const onResize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setSize({ w: window.innerWidth, h: window.innerHeight }));
    };
    onResize();
    window.addEventListener('resize', onResize);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', onResize);
    };
  }, []);

  const value = useMemo(() => deriveViewport(size.w, size.h), [size.w, size.h]);
  return <ViewportContext.Provider value={value}>{children}</ViewportContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useViewport(): Viewport {
  return useContext(ViewportContext);
}
