import { describe, expect, it } from 'vitest';
import { pageToPath, pathToPage, PAGE_IDS } from '../routing';

describe('app routing helpers', () => {
  it('maps the home page to /app/engagements and back', () => {
    expect(pageToPath('dashboard')).toBe('/app/engagements');
    expect(pathToPage('/app/engagements')).toBe('dashboard');
  });
  it('round-trips every page id through a path', () => {
    for (const id of PAGE_IDS) {
      expect(pathToPage(pageToPath(id))).toBe(id);
    }
  });
  it('falls back to dashboard for unknown or bare /app paths', () => {
    expect(pathToPage('/app')).toBe('dashboard');
    expect(pathToPage('/app/nonsense')).toBe('dashboard');
  });
});
