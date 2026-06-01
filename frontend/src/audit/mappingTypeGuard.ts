import type { ColumnType } from './csv';
import type { ColumnMapping } from './types';
import type { MappingErrors } from './validateMapping';

// The backend coerces EVERY mapped column to numeric and drops rows that don't
// convert (audit_mapping.py: pd.to_numeric(errors="coerce") → dropna). So a
// non-numeric column (e.g. raw COMPAS `race`, `sex`, `c_charge_degree`) silently
// nukes the whole dataset → "0 usable rows". Catch it before submit and tell the
// user to encode categories as numbers — verified empirically against the engine.
const numericMsg = (col: string) =>
  `Column "${col}" looks non-numeric. The audit converts every mapped column to numbers and drops rows that don't convert, so encode categories as numbers first (e.g. 0/1).`;

/** Per-role errors for any mapped column whose sampled values are non-numeric. */
export function nonNumericMappingErrors(
  mapping: ColumnMapping,
  types: Record<string, ColumnType>,
): MappingErrors {
  const errs: MappingErrors = {};
  if (mapping.treatment && types[mapping.treatment] === 'string') errs.treatment = numericMsg(mapping.treatment);
  if (mapping.outcome && types[mapping.outcome] === 'string') errs.outcome = numericMsg(mapping.outcome);
  const badConf = (mapping.confounders ?? []).filter((c) => types[c] === 'string');
  if (badConf.length > 0) {
    errs.confounders = `Non-numeric confounder${badConf.length > 1 ? 's' : ''}: ${badConf.join(', ')}. Encode as numbers (e.g. 0/1) before uploading.`;
  }
  if (mapping.instrument && types[mapping.instrument] === 'string') errs.instrument = numericMsg(mapping.instrument);
  return errs;
}
