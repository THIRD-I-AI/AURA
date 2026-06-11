import type { ColumnType } from './csv';
import type { ColumnMapping } from './types';
import type { MappingErrors } from './validateMapping';

// The backend now AUTO-ENCODES categoricals (audit_mapping.encode_for_audit):
// a 2-value treatment/outcome/instrument → 0/1, and a low-cardinality categorical
// confounder → one-hot. It still REJECTS a >2-category treatment/outcome/instrument
// (the contrast must be binary) and a high-cardinality categorical confounder.
// So the wizard blocks only what the backend rejects, and shows a non-blocking
// note for what it will auto-encode — keeping the UI in lockstep with the engine.
const CARD_CAP = 12; // mirrors audit_mapping.CARD_CAP

export interface MappingGuard {
  errors: MappingErrors; // blocking — the backend would reject these
  notes: MappingErrors; // non-blocking — the backend will auto-encode these
}

function distinctCount(col: string, columns: string[], sampleRows: string[][]): number {
  const idx = columns.indexOf(col);
  if (idx < 0) return 0;
  return new Set(sampleRows.map((r) => r[idx]).filter((v) => v !== undefined && v !== '')).size;
}

export function mappingTypeGuard(
  mapping: ColumnMapping,
  columns: string[],
  types: Record<string, ColumnType>,
  sampleRows: string[][],
  cardCap: number = CARD_CAP,
): MappingGuard {
  const errors: MappingErrors = {};
  const notes: MappingErrors = {};
  const isCategorical = (c?: string): c is string => !!c && types[c] === 'string';

  // Treatment & instrument must reduce to a binary contrast.
  for (const role of ['treatment', 'instrument'] as const) {
    const col = mapping[role];
    if (!isCategorical(col)) continue;
    if (distinctCount(col, columns, sampleRows) > 2) {
      errors[role] = `${role === 'treatment' ? 'Treatment' : 'Instrument'} "${col}" has more than 2 categories; the audit needs a binary contrast — filter your data to two groups.`;
    } else {
      notes[role] = `"${col}" will be auto-encoded to 0/1.`;
    }
  }

  // Outcome must be numeric or binary.
  if (isCategorical(mapping.outcome)) {
    if (distinctCount(mapping.outcome, columns, sampleRows) > 2) {
      errors.outcome = `Outcome "${mapping.outcome}" has more than 2 categories; it must be numeric or binary.`;
    } else {
      notes.outcome = `"${mapping.outcome}" will be auto-encoded to 0/1.`;
    }
  }

  // Categorical confounders are one-hot encoded up to the cardinality cap.
  const conf = mapping.confounders ?? [];
  const highCard = conf.filter((c) => isCategorical(c) && distinctCount(c, columns, sampleRows) > cardCap);
  const lowCard = conf.filter((c) => isCategorical(c) && distinctCount(c, columns, sampleRows) <= cardCap);
  if (highCard.length > 0) {
    errors.confounders = `High-cardinality confounder${highCard.length > 1 ? 's' : ''}: ${highCard.join(', ')} (> ${cardCap} categories). Drop or bucket before auditing.`;
  } else if (lowCard.length > 0) {
    notes.confounders = `Categorical confounder${lowCard.length > 1 ? 's' : ''} ${lowCard.join(', ')} will be one-hot encoded.`;
  }

  return { errors, notes };
}
