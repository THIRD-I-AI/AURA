import type { ColumnMapping } from '../types';

export function ReviewStep({ filename, mapping }: { filename: string | null; mapping: ColumnMapping }) {
  const row = (label: string, value: string) => (
    <div className="flex justify-between py-2 border-b border-border">
      <span className="text-text-tertiary">{label}</span>
      <span className="font-mono">{value}</span>
    </div>
  );
  return (
    <div data-testid="wizard-step-review">
      <h3>3 · Review &amp; run</h3>
      {row('Dataset', filename ?? '—')}
      {row('Treatment', mapping.treatment)}
      {row('Outcome', mapping.outcome)}
      {row('Confounders', mapping.confounders.join(', '))}
      {row('Instrument', mapping.instrument ?? 'none')}
    </div>
  );
}
