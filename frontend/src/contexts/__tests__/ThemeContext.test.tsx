import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ThemeProvider } from '../ThemeContext';

// BUG-113: the persisted theme used to be an unchecked `as Theme` cast --
// any corrupted/stale/tampered localStorage value flowed straight into the
// DOM attribute and was written right back, persisting the bad value forever.
describe('BUG-113: ThemeProvider validates the persisted theme value', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('falls back to the dark default when localStorage holds an invalid value', () => {
    localStorage.setItem('aura-theme', 'not-a-real-theme');

    render(<ThemeProvider><div /></ThemeProvider>);

    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(localStorage.getItem('aura-theme')).toBe('dark');
  });

  it('still honors a valid saved "light" preference', () => {
    localStorage.setItem('aura-theme', 'light');

    render(<ThemeProvider><div /></ThemeProvider>);

    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('defaults to dark when nothing is saved', () => {
    render(<ThemeProvider><div /></ThemeProvider>);

    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });
});
