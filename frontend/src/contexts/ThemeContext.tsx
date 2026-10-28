import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

interface ThemeContextType {
  theme: Theme;
  toggleTheme: () => void;
  setTheme: (theme: Theme) => void;
}

const STORAGE_KEY = 'aura-theme';

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

function readSaved(): Theme | null {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
    // BUG-113: an unrecognised value (corrupted, stale, tampered) is dropped,
    // never cast and written back.
    if (saved !== null) window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage blocked: the system setting decides */
  }
  return null;
}

function systemTheme(): Theme {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

interface ThemeProviderProps {
  children: React.ReactNode;
}

/** Dark and light are both first-class. Until the visitor picks one, the
 *  system setting decides and nothing is stored; public/theme-init.js applies
 *  the same rule before first paint so neither theme flashes. */
export const ThemeProvider: React.FC<ThemeProviderProps> = ({ children }) => {
  const [theme, setThemeState] = useState<Theme>(() => readSaved() ?? systemTheme());

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* storage blocked: the choice lasts for this page only */
    }
  }, []);

  const toggleTheme = useCallback(() => setTheme(theme === 'light' ? 'dark' : 'light'), [setTheme, theme]);

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components -- the hook belongs with its provider
export function useTheme(): ThemeContextType {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used inside ThemeProvider');
  return ctx;
}
