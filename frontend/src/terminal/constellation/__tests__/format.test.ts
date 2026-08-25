import { describe, expect, it } from 'vitest';
import { formatBytes } from '../format';

describe('formatBytes', () => {
  it('formats zero and sub-KB sizes as whole bytes', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
  });

  it('formats KB/MB/GB with one decimal place', () => {
    expect(formatBytes(512 * 1024)).toBe('512.0 KB');
    expect(formatBytes(2.3 * 1024 * 1024)).toBe('2.3 MB');
    expect(formatBytes(1024 * 1024 * 1024)).toBe('1.0 GB');
  });
});
