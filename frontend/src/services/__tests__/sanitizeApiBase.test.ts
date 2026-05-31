import { describe, expect, it } from 'vitest';

import { sanitizeApiBase } from '../api';

const FALLBACK = 'http://localhost:8000';

describe('sanitizeApiBase', () => {
  it('accepts absolute http/https URLs', () => {
    expect(sanitizeApiBase('https://api.aura.example', FALLBACK)).toBe('https://api.aura.example');
    expect(sanitizeApiBase('http://10.0.0.5:9000', FALLBACK)).toBe('http://10.0.0.5:9000');
  });

  it('rejects javascript: and data: schemes (CodeQL js/xss-through-dom source)', () => {
    expect(sanitizeApiBase('javascript:alert(1)//', FALLBACK)).toBe(FALLBACK);
    expect(sanitizeApiBase('data:text/html,<script>alert(1)</script>', FALLBACK)).toBe(FALLBACK);
  });

  it('falls back on empty, null, or malformed values', () => {
    expect(sanitizeApiBase(null, FALLBACK)).toBe(FALLBACK);
    expect(sanitizeApiBase('', FALLBACK)).toBe(FALLBACK);
    expect(sanitizeApiBase('not a url', FALLBACK)).toBe(FALLBACK);
    expect(sanitizeApiBase('/relative/path', FALLBACK)).toBe(FALLBACK);
  });
});
