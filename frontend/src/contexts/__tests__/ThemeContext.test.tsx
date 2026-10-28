import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ThemeProvider, useTheme } from '../ThemeContext';

function Probe() {
  const { theme, toggleTheme } = useTheme();
  return <button onClick={toggleTheme}>{theme}</button>;
}

// BUG-113: the persisted theme used to be an unchecked `as Theme` cast --
// any corrupted/stale/tampered localStorage value flowed straight into the
// DOM attribute and was written right back, persisting the bad value forever.
describe('ThemeProvider', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('drops an invalid saved value and falls back to the system setting (dark here)', () => {
    localStorage.setItem('aura-theme', 'not-a-real-theme');

    render(<ThemeProvider><div /></ThemeProvider>);

    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(localStorage.getItem('aura-theme')).toBeNull();
  });

  it('honors a saved "light" preference', () => {
    localStorage.setItem('aura-theme', 'light');

    render(<ThemeProvider><div /></ThemeProvider>);

    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('follows the system setting without storing anything until the visitor chooses', () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);

    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(localStorage.getItem('aura-theme')).toBeNull();

    act(() => screen.getByRole('button').click());

    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(localStorage.getItem('aura-theme')).toBe('light');
  });
});
