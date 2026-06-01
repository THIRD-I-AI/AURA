import { describe, expect, it } from 'vitest';
import { parseCsvHeadAndRows } from '../csv';

describe('parseCsvHeadAndRows', () => {
  it('parses a simple CSV into columns + rows', () => {
    const out = parseCsvHeadAndRows('a,b,c\n1,2,3\n4,5,6\n');
    expect(out.columns).toEqual(['a', 'b', 'c']);
    expect(out.rows).toEqual([['1', '2', '3'], ['4', '5', '6']]);
  });

  it('handles quoted fields containing commas', () => {
    const out = parseCsvHeadAndRows('name,note\n"Doe, John","says ""hi"""\n');
    expect(out.columns).toEqual(['name', 'note']);
    expect(out.rows[0]).toEqual(['Doe, John', 'says "hi"']);
  });

  it('handles CRLF line endings', () => {
    const out = parseCsvHeadAndRows('a,b\r\n1,2\r\n');
    expect(out.columns).toEqual(['a', 'b']);
    expect(out.rows).toEqual([['1', '2']]);
  });

  it('infers number vs string column types from sampled rows', () => {
    const out = parseCsvHeadAndRows('age,name\n30,alice\n40,bob\n');
    expect(out.types).toEqual({ age: 'number', name: 'string' });
  });

  it('returns empty columns for empty/whitespace input without throwing', () => {
    expect(parseCsvHeadAndRows('').columns).toEqual([]);
    expect(parseCsvHeadAndRows('   \n  ').columns).toEqual([]);
  });

  it('caps rows at maxRows', () => {
    const text = 'a\n' + Array.from({ length: 50 }, (_, i) => String(i)).join('\n');
    expect(parseCsvHeadAndRows(text, 10).rows).toHaveLength(10);
  });
});
