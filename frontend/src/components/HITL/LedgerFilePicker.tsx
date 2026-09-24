/* Per-dataset CSV/JSON file inputs for "Audit your ledger" (BUG-158). Parsing
   and validation live in audit/ledgerFiles.ts; this only renders the picker
   and each file's outcome so a bad file is explained before anything is sent. */
import { Button } from '@/components/ui-kit/button';
import { cn } from '@/lib/cn';
import { LEDGER_DATASETS, type LedgerDatasetKey, type LedgerDatasetSpec, type ParsedLedgerFile } from '../../audit/ledgerFiles';

export type LoadedLedgerFiles = Partial<Record<LedgerDatasetKey, { name: string; result: ParsedLedgerFile }>>;

const FILE_INPUT_CLASS = cn(
  'block w-full rounded-none border border-border bg-card p-1.5 font-mono text-2xs text-card-foreground',
  'file:mr-2 file:rounded-none file:border file:border-border-hairline file:bg-secondary file:px-2 file:py-0.5 file:font-mono file:text-2xs',
  'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none',
  'disabled:pointer-events-none disabled:opacity-50',
);

export function LedgerFilePicker({
  files, disabled, onLoad, onRemove,
}: {
  files: LoadedLedgerFiles;
  disabled: boolean;
  onLoad: (spec: LedgerDatasetSpec, file: File) => void;
  onRemove: (key: LedgerDatasetKey) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" data-testid="ledger-file-picker">
      {LEDGER_DATASETS.map((spec) => {
        const entry = files[spec.key];
        const id = `ledger-file-input-${spec.key}`;
        return (
          <div key={spec.key} data-testid={`ledger-file-${spec.key}`} className="flex flex-col gap-1.5 border border-border-hairline p-2">
            <label htmlFor={id} className="font-mono text-2xs font-semibold text-text-secondary">{spec.label}</label>
            <input
              id={id}
              type="file"
              accept=".csv,.json,text/csv,application/json"
              disabled={disabled}
              className={FILE_INPUT_CLASS}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) onLoad(spec, file);
                // Let the same file be chosen again after a fix.
                e.target.value = '';
              }}
            />
            {entry && (
              <div className="flex flex-col gap-1" data-testid={`ledger-file-status-${spec.key}`}>
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-2xs text-text-tertiary" title={entry.name}>{entry.name}</span>
                  <Button type="button" variant="ghost" size="xs" disabled={disabled} onClick={() => onRemove(spec.key)}>
                    Remove
                  </Button>
                </div>
                {entry.result.errors.map((msg) => (
                  <p key={msg} role="alert" className="font-mono text-2xs text-danger">{msg}</p>
                ))}
                {entry.result.errors.length === 0 && (
                  <p className="font-mono text-2xs text-signal">
                    {entry.result.rows.length.toLocaleString()} row{entry.result.rows.length === 1 ? '' : 's'} · {entry.result.columns.join(', ')}
                  </p>
                )}
                {entry.result.warnings.map((msg) => (
                  <p key={msg} role="note" className="font-mono text-2xs text-warn">{msg}</p>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
