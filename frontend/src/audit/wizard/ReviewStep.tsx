import type { ColumnMapping } from '../types';

export function ReviewStep({ filename, mapping }: { filename: string | null; mapping: ColumnMapping }) {
  const row = (label: string, value: string) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: 'var(--space-2) 0', borderBottom: '1px solid var(--border-default)' }}>
      <span style={{ color: 'var(--text-tertiary)' }}>{label}</span>
      <span style={{ fontFamily: 'monospace' }}>{value}</span>
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
