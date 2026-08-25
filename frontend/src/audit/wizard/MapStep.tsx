import { cn } from '@/lib/cn';
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
  const fieldClass = 'w-full p-3 bg-base border border-border rounded-none text-text-primary';

  const toggleConfounder = (col: string) => {
    const next = mapping.confounders.includes(col)
      ? mapping.confounders.filter((c) => c !== col)
      : [...mapping.confounders, col];
    set({ confounders: next });
  };

  return (
    <div data-testid="wizard-step-map">
      <h3>2 · Map columns to causal roles</h3>

      <label className="block mb-4">
        <span className="text-sm text-text-secondary">Treatment</span>
        <select data-testid="map-treatment" value={mapping.treatment} onChange={(e) => set({ treatment: e.target.value })} className={fieldClass}>
          <option value="">— select —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.treatment && <span data-testid="err-treatment" className="text-danger text-xs">{errors.treatment}</span>}
        {!errors.treatment && notes.treatment && <span data-testid="note-treatment" className="text-text-tertiary text-xs">{notes.treatment}</span>}
      </label>

      <label className="block mb-4">
        <span className="text-sm text-text-secondary">Outcome</span>
        <select data-testid="map-outcome" value={mapping.outcome} onChange={(e) => set({ outcome: e.target.value })} className={fieldClass}>
          <option value="">— select —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.outcome && <span data-testid="err-outcome" className="text-danger text-xs">{errors.outcome}</span>}
        {!errors.outcome && notes.outcome && <span data-testid="note-outcome" className="text-text-tertiary text-xs">{notes.outcome}</span>}
      </label>

      <div className="mb-4">
        <span className="text-sm text-text-secondary">Confounders</span>
        <div data-testid="map-confounders" className="flex flex-wrap gap-2 mt-1">
          {columns.map((c) => (
            <button key={c} type="button" data-testid={`confounder-${c}`} onClick={() => toggleConfounder(c)}
              className={cn(
                'py-1 px-3 rounded-none cursor-pointer border',
                mapping.confounders.includes(c)
                  ? 'border-signal bg-brand text-[var(--text-on-accent)]'
                  : 'border-border bg-transparent text-text-secondary',
              )}>
              {c}
            </button>
          ))}
        </div>
        {errors.confounders && <span data-testid="err-confounders" className="text-danger text-xs">{errors.confounders}</span>}
        {!errors.confounders && notes.confounders && <span data-testid="note-confounders" className="block text-text-tertiary text-xs">{notes.confounders}</span>}
      </div>

      <label className="block mb-4">
        <span className="text-sm text-text-secondary">Instrument (optional — enables IV)</span>
        <select data-testid="map-instrument" value={mapping.instrument ?? ''} onChange={(e) => set({ instrument: e.target.value || undefined })} className={fieldClass}>
          <option value="">— none —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.instrument && <span data-testid="err-instrument" className="text-danger text-xs">{errors.instrument}</span>}
        {!errors.instrument && notes.instrument && <span data-testid="note-instrument" className="text-text-tertiary text-xs">{notes.instrument}</span>}
      </label>
    </div>
  );
}
