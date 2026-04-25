import '@testing-library/jest-dom/vitest';

// jsdom doesn't implement scrollIntoView; stub so components that auto-scroll
// (e.g. ChatInterface) don't blow up in tests.
if (typeof window !== 'undefined' && !(Element.prototype as { scrollIntoView?: unknown }).scrollIntoView) {
  Element.prototype.scrollIntoView = function () {};
}
