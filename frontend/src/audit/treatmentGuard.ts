import type { ColumnType } from './csv';

/**
 * The backend binarizes the treatment: a numeric column is split at its median,
 * and a column with exactly two values is mapped to 0/1. But a NON-numeric column
 * with more than two categories hits the median branch (`float(median())` on
 * strings), which fails. Catch that here, before submit, with guidance.
 *
 * Numeric treatments never trip this (they binarize fine). The sample is the CSV
 * preview, so ">2 distinct" is a reliable lower bound — if the sample already
 * shows 3+ string categories, the full file does too.
 *
 * Returns a blocking error message, or null when the treatment is acceptable.
 */
export function treatmentCardinalityError(
  treatment: string,
  columns: string[],
  types: Record<string, ColumnType>,
  sampleRows: string[][],
): string | null {
  if (!treatment) return null;            // "required" is validateMapping's job
  if (types[treatment] !== 'string') return null;  // numeric → binarized, fine
  const idx = columns.indexOf(treatment);
  if (idx < 0) return null;
  const distinct = new Set(sampleRows.map((r) => r[idx]).filter((v) => v !== undefined && v !== ''));
  if (distinct.size > 2) {
    return `Treatment "${treatment}" looks categorical with ${distinct.size}+ values. The audit binarizes the treatment, so filter your data to exactly two groups (e.g., two categories) for a clean causal contrast.`;
  }
  return null;
}
