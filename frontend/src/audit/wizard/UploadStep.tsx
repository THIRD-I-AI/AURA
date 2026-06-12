import { useRef, useState } from 'react';

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
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  return (
    <div data-testid="wizard-step-upload">
      <h3>1 · Upload your dataset</h3>
      <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-sm)' }}>A CSV with one row per decision. We parse it in your browser instantly; nothing is shared until you run the audit.</p>
      {/* The native input stays in the DOM (hidden) so programmatic
          uploads — and the existing wizard tests — keep working. */}
      <input
        ref={inputRef}
        data-testid="wizard-file-input"
        type="file"
        accept=".csv,text/csv"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onPick(f); }}
        style={{ display: 'none' }}
      />
      <div
        data-testid="wizard-dropzone"
        role="button"
        tabIndex={0}
        aria-label="Upload CSV. Drag and drop, or press Enter to browse."
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click(); }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const f = e.dataTransfer.files?.[0];
          if (f) onPick(f);
        }}
        style={{
          margin: 'var(--space-4) 0',
          padding: 'var(--space-7) var(--space-5)',
          textAlign: 'center',
          cursor: 'pointer',
          border: `1.5px dashed ${dragOver ? 'var(--accent)' : 'var(--border-default)'}`,
          borderRadius: 'var(--radius-md)',
          background: dragOver ? 'rgba(56, 130, 246, 0.06)' : 'var(--card-bg, rgba(15, 23, 42, 0.5))',
          transition: 'border-color 120ms ease, background 120ms ease',
        }}
      >
        <div aria-hidden="true" style={{ fontSize: 22, marginBottom: 'var(--space-2)', color: 'var(--accent)' }}>↑</div>
        {file ? (
          <p style={{ margin: 0, fontWeight: 600 }}>{file.name}</p>
        ) : (
          <p style={{ margin: 0, fontWeight: 600 }}>Drag &amp; drop your CSV here</p>
        )}
        <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>
          {file ? 'Click to choose a different file' : 'or click to browse'}
        </p>
      </div>
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
