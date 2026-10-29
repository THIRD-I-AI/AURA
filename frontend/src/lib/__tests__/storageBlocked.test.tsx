import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// BUG-552: a browser that blocks site storage throws a SecurityError from the
// window.localStorage getter itself, which used to escape api.ts at import time
// and blank every route.
function findDescriptor(): { owner: object; desc: PropertyDescriptor } {
  let owner: object | null = window;
  while (owner) {
    const desc = Object.getOwnPropertyDescriptor(owner, 'localStorage');
    if (desc) return { owner, desc };
    owner = Object.getPrototypeOf(owner);
  }
  throw new Error('localStorage descriptor not found');
}

describe('BUG-552: blocked site storage', () => {
  const original = findDescriptor();

  beforeEach(() => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('The operation is insecure.', 'SecurityError');
      },
    });
    vi.resetModules();
  });

  afterEach(() => {
    if (original.owner === window) {
      Object.defineProperty(window, 'localStorage', original.desc);
    } else {
      delete (window as unknown as Record<string, unknown>).localStorage;
    }
  });

  it('the getter really throws in this setup', () => {
    expect(() => window.localStorage).toThrow();
  });

  it('api.ts imports and falls back to defaults', async () => {
    const api = await import('../../services/api');
    expect(api.API_BASE_URL).toMatch(/\/api\/v1$/);
    expect(api.getAuthToken()).toBeNull();
  });

  it('ThemeProvider renders with the dark default', async () => {
    const { ThemeProvider } = await import('../../contexts/ThemeContext');
    render(<ThemeProvider><div>themed child</div></ThemeProvider>);
    expect(screen.getByText('themed child')).toBeTruthy();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('restoreLayout reports no saved layout instead of throwing', async () => {
    const { restoreLayout } = await import('../../terminal/layoutStore');
    expect(restoreLayout('analyst', { fromJSON: vi.fn() })).toBe(false);
  });
});
