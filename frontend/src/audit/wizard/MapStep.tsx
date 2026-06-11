import type { ColumnMapping } from '../types';
import type { MappingErrors } from '../validateMapping';

export function MapStep({ columns, mapping, errors, notes = {}, onChange }: {
  columns: string[];
  mapping: ColumnMapping;
  errors: MappingErrors;
  notes?: MappingErrors;
  onChange: (next: ColumnMapping) => void;
}) {
  const set = (patch: Partial<ColumnMapping>) => onChange({ ...mapping, ...patch });
  const fieldStyle = { width: '100%', padding: 'var(--space-3)', background: 'var(--bg-base)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)' };

  const toggleConfounder = (col: string) => {
    const next = mapping.confounders.includes(col)
      ? mapping.confounders.filter((c) => c !== col)
      : [...mapping.confounders, col];
    set({ confounders: next });
  };

  return (
    <div data-testid="wizard-step-map">
      <h3>2 · Map columns to causal roles</h3>

      <label style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
        <span style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>Treatment</span>
        <select data-testid="map-treatment" value={mapping.treatment} onChange={(e) => set({ treatment: e.target.value })} style={fieldStyle}>
          <option value="">— select —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.treatment && <span data-testid="err-treatment" style={{ color: 'var(--red)', fontSize: 'var(--font-xs)' }}>{errors.treatment}</span>}
        {!errors.treatment && notes.treatment && <span data-testid="note-treatment" style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-xs)' }}>{notes.treatment}</span>}
      </label>

      <label style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
        <span style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>Outcome</span>
        <select data-testid="map-outcome" value={mapping.outcome} onChange={(e) => set({ outcome: e.target.value })} style={fieldStyle}>
          <option value="">— select —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.outcome && <span data-testid="err-outcome" style={{ color: 'var(--red)', fontSize: 'var(--font-xs)' }}>{errors.outcome}</span>}
        {!errors.outcome && notes.outcome && <span data-testid="note-outcome" style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-xs)' }}>{notes.outcome}</span>}
      </label>

      <div style={{ marginBottom: 'var(--space-4)' }}>
        <span style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>Confounders</span>
        <div data-testid="map-confounders" style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginTop: 'var(--space-1)' }}>
          {columns.map((c) => (
            <button key={c} type="button" data-testid={`confounder-${c}`} onClick={() => toggleConfounder(c)}
              style={{ padding: 'var(--space-1) var(--space-3)', borderRadius: 'var(--radius-full)', cursor: 'pointer',
                border: `1px solid ${mapping.confounders.includes(c) ? 'var(--accent)' : 'var(--border-default)'}`,
                background: mapping.confounders.includes(c) ? 'var(--accent)' : 'transparent',
                color: mapping.confounders.includes(c) ? '#fff' : 'var(--text-secondary)' }}>
              {c}
            </button>
          ))}
        </div>
        {errors.confounders && <span data-testid="err-confounders" style={{ color: 'var(--red)', fontSize: 'var(--font-xs)' }}>{errors.confounders}</span>}
        {!errors.confounders && notes.confounders && <span data-testid="note-confounders" style={{ display: 'block', color: 'var(--text-tertiary)', fontSize: 'var(--font-xs)' }}>{notes.confounders}</span>}
      </div>

      <label style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
        <span style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>Instrument (optional — enables IV)</span>
        <select data-testid="map-instrument" value={mapping.instrument ?? ''} onChange={(e) => set({ instrument: e.target.value || undefined })} style={fieldStyle}>
          <option value="">— none —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.instrument && <span data-testid="err-instrument" style={{ color: 'var(--red)', fontSize: 'var(--font-xs)' }}>{errors.instrument}</span>}
        {!errors.instrument && notes.instrument && <span data-testid="note-instrument" style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-xs)' }}>{notes.instrument}</span>}
      </label>
    </div>
  );
}
