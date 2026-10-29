// BUG-552: with site storage blocked ("Don't allow sites to save data", hardened
// profiles) merely touching window.localStorage throws a SecurityError, and a
// `typeof localStorage` check runs the same getter. Reads and writes that run on
// import or during render go through these, so blocked storage degrades to
// "nothing saved" instead of a blank page.
export function safeGetItem(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function safeSetItem(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch { /* storage blocked */ }
}

export function safeRemoveItem(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch { /* storage blocked */ }
}
