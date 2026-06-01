import { describe, expect, it } from 'vitest';
import { validateMapping } from '../validateMapping';
import type { ColumnMapping } from '../types';

const COLS = ['t', 'y', 'c1', 'c2', 'iv'];
const base: ColumnMapping = { treatment: 't', outcome: 'y', confounders: ['c1'] };

describe('validateMapping', () => {
  it('accepts a complete valid mapping', () => {
    expect(validateMapping(base, COLS).valid).toBe(true);
  });

  it('requires treatment, outcome, and at least one confounder', () => {
    const r = validateMapping({ treatment: '', outcome: '', confounders: [] }, COLS);
    expect(r.valid).toBe(false);
    expect(r.errors.treatment).toBeTruthy();
    expect(r.errors.outcome).toBeTruthy();
    expect(r.errors.confounders).toBeTruthy();
  });

  it('rejects treatment === outcome', () => {
    expect(validateMapping({ ...base, outcome: 't' }, COLS).errors.outcome).toBeTruthy();
  });

  it('rejects a confounder that is the treatment or outcome', () => {
    expect(validateMapping({ ...base, confounders: ['t'] }, COLS).errors.confounders).toBeTruthy();
    expect(validateMapping({ ...base, confounders: ['y'] }, COLS).errors.confounders).toBeTruthy();
  });

  it('rejects an instrument that collides with another role', () => {
    expect(validateMapping({ ...base, instrument: 't' }, COLS).errors.instrument).toBeTruthy();
    expect(validateMapping({ ...base, instrument: 'c1' }, COLS).errors.instrument).toBeTruthy();
  });

  it('accepts a valid distinct instrument', () => {
    expect(validateMapping({ ...base, instrument: 'iv' }, COLS).valid).toBe(true);
  });

  it('rejects a column not present in the parsed header (stale mapping)', () => {
    expect(validateMapping({ ...base, treatment: 'gone' }, COLS).errors.treatment).toBeTruthy();
  });
});
