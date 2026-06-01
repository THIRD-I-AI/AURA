import { describe, expect, it } from 'vitest';
import { treatmentCardinalityError } from '../treatmentGuard';
import type { ColumnType } from '../csv';

const cols = ['race', 'recid', 'priors'];
const types: Record<string, ColumnType> = { race: 'string', recid: 'number', priors: 'number' };

describe('treatmentCardinalityError', () => {
  it('blocks a non-numeric treatment with >2 categories', () => {
    const rows = [['African-American', '1', '3'], ['Caucasian', '0', '1'], ['Hispanic', '1', '2']];
    const err = treatmentCardinalityError('race', cols, types, rows);
    expect(err).toMatch(/two groups/i);
    expect(err).toContain('race');
  });

  it('allows a non-numeric treatment with exactly two categories', () => {
    const rows = [['African-American', '1', '3'], ['Caucasian', '0', '1'], ['African-American', '0', '2']];
    expect(treatmentCardinalityError('race', cols, types, rows)).toBeNull();
  });

  it('never blocks a numeric treatment, even with many distinct values', () => {
    const numTypes: Record<string, ColumnType> = { priors: 'number' };
    const rows = [['0'], ['1'], ['2'], ['5'], ['9']];
    expect(treatmentCardinalityError('priors', ['priors'], numTypes, rows)).toBeNull();
  });

  it('returns null when no treatment is selected or column is unknown', () => {
    expect(treatmentCardinalityError('', cols, types, [])).toBeNull();
    expect(treatmentCardinalityError('missing', cols, types, [['x']])).toBeNull();
  });
});
