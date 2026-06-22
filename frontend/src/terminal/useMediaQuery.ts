import { useEffect, useState } from 'react';

/**
 * Subscribe to a CSS media query and re-render when it starts/stops matching.
 *
 * Test/SSR-safe: jsdom (and Node) have no `window.matchMedia`, so when it is
 * unavailable the hook reports `false` instead of throwing. That also keeps the
 * desktop render path the default everywhere matchMedia is absent.
 */
export function useMediaQuery(query: string): boolean {
  const getMatch = () =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false;

  const [matches, setMatches] = useState<boolean>(getMatch);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange(); // sync in case the query changed between render and effect
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}
