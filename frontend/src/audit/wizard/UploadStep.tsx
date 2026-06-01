import type { ColumnType } from '../csv';

export function UploadStep({ file, columns, previewRows, types, uploading, error, onPick }: {
  file: File | null;
  columns: string[];
  previewRows: string[][];
  types: Record<string, ColumnType>;
  uploading: boolean;
  error: string | null;
  onPick: (file: File) => void;
}) {
  return (
    <div data-testid="wizard-step-upload">
      <h3>1 · Upload your dataset</h3>
      <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-sm)' }}>A CSV with one row per decision. We parse it in your browser instantly; nothing is shared until you run the audit.</p>
      <input
        data-testid="wizard-file-input"
        type="file"
        accept=".csv,text/csv"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onPick(f); }}
        style={{ display: 'block', margin: 'var(--space-4) 0' }}
      />
      {uploading && <p data-testid="wizard-uploading" style={{ fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>Uploading {file?.name}…</p>}
      {error && <p data-testid="wizard-upload-error" style={{ color: 'var(--red)' }}>{error}</p>}
      {columns.length > 0 && (
        <div data-testid="wizard-preview" style={{ overflowX: 'auto', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 'var(--font-sm)', width: '100%' }}>
            <thead>
              <tr>{columns.map((c) => (
                <th key={c} style={{ textAlign: 'left', padding: 'var(--space-2)', borderBottom: '1px solid var(--border-default)' }}>
                  {c} <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>({types[c]})</span>
                </th>
              ))}</tr>
            </thead>
            <tbody>
              {previewRows.slice(0, 5).map((r, i) => (
                <tr key={i}>{columns.map((_, j) => <td key={j} style={{ padding: 'var(--space-2)', borderBottom: '1px solid var(--border-default)' }}>{r[j]}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
