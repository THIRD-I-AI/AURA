import { describe, expect, it } from 'vitest';
import { mappingTypeGuard } from '../mappingTypeGuard';
import type { ColumnType } from '../csv';
import type { ColumnMapping } from '../types';

const types: Record<string, ColumnType> = {
  race2: 'string', race3: 'string', sex: 'string', zip: 'string',
  recid: 'number', priors: 'number', recid_str: 'string',
};
// columns + a sample whose distinct counts drive the guard.
const columns = ['race2', 'race3', 'sex', 'zip', 'recid', 'priors', 'recid_str'];
const rows = [
  ['AA', 'AA', 'M', 'z1', '1', '3', 'yes'],
  ['Cauc', 'Cauc', 'F', 'z2', '0', '1', 'no'],
  ['AA', 'Hispanic', 'M', 'z3', '1', '2', 'yes'],
  ['Cauc', 'Other', 'F', 'z4', '0', '0', 'no'],
];

function guard(m: ColumnMapping) {
  return mappingTypeGuard(m, columns, types, rows, /* cardCap */ 3);
}

describe('mappingTypeGuard', () => {
  it('notes (does NOT block) a 2-category treatment — backend auto-encodes it', () => {
    const g = guard({ treatment: 'race2', outcome: 'recid', confounders: ['priors'] });
    expect(g.errors.treatment).toBeUndefined();
    expect(g.notes.treatment).toMatch(/auto-encoded to 0\/1/i);
  });

  it('blocks a >2-category treatment (backend requires a binary contrast)', () => {
    const g = guard({ treatment: 'race3', outcome: 'recid', confounders: ['priors'] });
    expect(g.errors.treatment).toMatch(/two groups|binary/i);
    expect(g.notes.treatment).toBeUndefined();
  });

  it('notes a low-cardinality categorical confounder (one-hot) but does not block', () => {
    const g = guard({ treatment: 'recid', outcome: 'recid', confounders: ['sex'] });
    expect(g.errors.confounders).toBeUndefined();
    expect(g.notes.confounders).toMatch(/one-hot/i);
  });

  it('blocks a high-cardinality categorical confounder', () => {
    // zip has 4 distinct in the sample; cardCap=3 → high-cardinality.
    const g = guard({ treatment: 'recid', outcome: 'recid', confounders: ['zip'] });
    expect(g.errors.confounders).toMatch(/high-cardinality|bucket/i);
  });

  it('blocks a >2-category outcome but notes a binary string outcome', () => {
    expect(guard({ treatment: 'race2', outcome: 'race3', confounders: ['priors'] }).errors.outcome).toBeTruthy();
    expect(guard({ treatment: 'priors', outcome: 'recid_str', confounders: ['priors'] }).notes.outcome).toMatch(/0\/1/);
  });

  it('numeric columns produce no errors and no notes', () => {
    const g = guard({ treatment: 'recid', outcome: 'priors', confounders: ['priors'] });
    expect(g.errors).toEqual({});
    expect(g.notes).toEqual({});
  });
});
