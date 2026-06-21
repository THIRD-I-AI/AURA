import { describe, expect, it } from 'vitest';
import { fuzzyScore } from '../fuzzyScore';

describe('fuzzyScore', () => {
  it('ranks exact > prefix > substring > subsequence > none', () => {
    expect(fuzzyScore('query', '')).toBe(1);
    expect(fuzzyScore('Query', 'query')).toBe(100);
    expect(fuzzyScore('Query Panel', 'query')).toBe(80);
    expect(fuzzyScore('Open Query', 'query')).toBe(60);
    expect(fuzzyScore('Query', 'qy')).toBeGreaterThan(0);
    expect(fuzzyScore('Query', 'zzz')).toBe(0);
  });
});
