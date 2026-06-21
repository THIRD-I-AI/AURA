import '@testing-library/jest-dom/vitest';

// jsdom doesn't implement scrollIntoView; stub so components that auto-scroll
// (e.g. ChatInterface) don't blow up in tests.
if (typeof window !== 'undefined' && !(Element.prototype as { scrollIntoView?: unknown }).scrollIntoView) {
  Element.prototype.scrollIntoView = function () {};
}

// dockview (and other layout engines) use ResizeObserver; jsdom doesn't ship it.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {} unobserve() {} disconnect() {}
  } as unknown as typeof ResizeObserver;
}

// jsdom may not expose localStorage in all vitest environments; provide a
// minimal in-memory stub so layoutStore (and similar) don't throw.
if (typeof globalThis.localStorage === 'undefined') {
  const _store: Record<string, string> = {};
  globalThis.localStorage = {
    getItem: (k: string) => _store[k] ?? null,
    setItem: (k: string, v: string) => { _store[k] = v; },
    removeItem: (k: string) => { delete _store[k]; },
    clear: () => { Object.keys(_store).forEach((k) => delete _store[k]); },
    key: (i: number) => Object.keys(_store)[i] ?? null,
    get length() { return Object.keys(_store).length; },
  } as Storage;
}
