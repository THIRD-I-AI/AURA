import { beforeEach, describe, expect, it } from 'vitest';
import { persistLayout, restoreLayout, DEFAULT_LAYOUTS, LAYOUT_NAMES } from '../layoutStore';

// Ensure localStorage is available in test environment
if (!global.localStorage) {
  const store: Record<string, string> = {};
  global.localStorage = {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { Object.keys(store).forEach(k => delete store[k]); },
    length: 0,
    key: () => null,
  } as any;
}

beforeEach(() => localStorage.clear());

describe('layoutStore', () => {
  it('round-trips a layout through localStorage', () => {
    const saved = { grid: { root: 'x' } };
    persistLayout('default', { toJSON: () => saved } as never);
    let restored: unknown = null;
    const ok = restoreLayout('default', { fromJSON: (d: unknown) => { restored = d; } } as never);
    expect(ok).toBe(true);
    expect(restored).toEqual(saved);
  });

  it('returns false when nothing is stored', () => {
    const ok = restoreLayout('default', { fromJSON: () => { throw new Error('should not be called'); } } as never);
    expect(ok).toBe(false);
  });

  it('falls back (returns false) on a corrupt stored value, without throwing', () => {
    localStorage.setItem('aura.terminal.layout.default', '{not json');
    const ok = restoreLayout('default', { fromJSON: () => {} } as never);
    expect(ok).toBe(false);
  });

  it('every default layout builder adds the four panels', () => {
    for (const name of LAYOUT_NAMES) {
      const ids: string[] = [];
      DEFAULT_LAYOUTS[name]({ addPanel: (o: { id: string }) => { ids.push(o.id); return {} as never; } } as never);
      expect(ids).toEqual(expect.arrayContaining(['query', 'datasets', 'findings', 'livefeed']));
    }
  });
});
