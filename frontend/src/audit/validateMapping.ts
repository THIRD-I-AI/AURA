import type { ColumnMapping } from './types';

export interface MappingErrors {
  treatment?: string;
  outcome?: string;
  confounders?: string;
  instrument?: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: MappingErrors;
}

export function validateMapping(m: ColumnMapping, columns: string[]): ValidationResult {
  const errors: MappingErrors = {};
  const has = (c: string) => columns.includes(c);

  if (!m.treatment) errors.treatment = 'Select a treatment column.';
  else if (!has(m.treatment)) errors.treatment = 'Column not found in file.';

  if (!m.outcome) errors.outcome = 'Select an outcome column.';
  else if (!has(m.outcome)) errors.outcome = 'Column not found in file.';
  else if (m.treatment && m.outcome === m.treatment) errors.outcome = 'Outcome must differ from treatment.';

  if (!m.confounders || m.confounders.length === 0) errors.confounders = 'Pick at least one confounder.';
  else if (m.confounders.some((c) => !has(c))) errors.confounders = 'A confounder is not in the file.';
  else if (m.treatment && m.confounders.includes(m.treatment)) errors.confounders = 'Confounders cannot include the treatment.';
  else if (m.outcome && m.confounders.includes(m.outcome)) errors.confounders = 'Confounders cannot include the outcome.';

  if (m.instrument) {
    if (!has(m.instrument)) errors.instrument = 'Column not found in file.';
    else if (m.instrument === m.treatment || m.instrument === m.outcome) errors.instrument = 'Instrument must be a distinct column.';
    else if (m.confounders?.includes(m.instrument)) errors.instrument = 'Instrument cannot also be a confounder.';
  }

  return { valid: Object.keys(errors).length === 0, errors };
}
