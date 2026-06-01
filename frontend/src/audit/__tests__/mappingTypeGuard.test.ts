import { describe, expect, it } from 'vitest';
import { nonNumericMappingErrors } from '../mappingTypeGuard';
import type { ColumnType } from '../csv';
import type { ColumnMapping } from '../types';

const types: Record<string, ColumnType> = {
  race: 'string', sex: 'string', recid: 'number', priors: 'number', age: 'number',
};

describe('nonNumericMappingErrors', () => {
  it('flags a non-numeric treatment regardless of cardinality (even 2 categories)', () => {
    const m: ColumnMapping = { treatment: 'race', outcome: 'recid', confounders: ['priors'] };
    expect(nonNumericMappingErrors(m, types).treatment).toMatch(/non-numeric|numbers/i);
  });

  it('flags non-numeric outcome, confounders, and instrument', () => {
    const m: ColumnMapping = { treatment: 'priors', outcome: 'race', confounders: ['sex', 'age'], instrument: 'race' };
    const errs = nonNumericMappingErrors(m, types);
    expect(errs.outcome).toBeTruthy();
    expect(errs.confounders).toContain('sex');
    expect(errs.instrument).toBeTruthy();
  });

  it('returns no errors when every mapped column is numeric', () => {
    const m: ColumnMapping = { treatment: 'race_encoded', outcome: 'recid', confounders: ['priors', 'age'] };
    const numTypes: Record<string, ColumnType> = { ...types, race_encoded: 'number' };
    expect(nonNumericMappingErrors(m, numTypes)).toEqual({});
  });

  it('ignores empty/unset roles', () => {
    const m: ColumnMapping = { treatment: '', outcome: '', confounders: [] };
    expect(nonNumericMappingErrors(m, types)).toEqual({});
  });
});
