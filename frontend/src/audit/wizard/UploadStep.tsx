import { useRef, useState } from 'react';

import { cn } from '@/lib/cn';
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
      <p className="text-text-tertiary text-sm">A CSV with one row per decision. We parse it in your browser instantly; nothing is shared until you run the audit.</p>
      {/* The native input stays in the DOM (hidden) so programmatic
          uploads — and the existing wizard tests — keep working. */}
      <input
        ref={inputRef}
        data-testid="wizard-file-input"
        type="file"
        accept=".csv,text/csv"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onPick(f); }}
        className="hidden"
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
        className={cn(
          'my-4 py-7 px-5 text-center cursor-pointer border-[1.5px] border-dashed rounded-none transition-[border-color,background-color] duration-150 ease-out',
          dragOver ? 'border-signal bg-signal/10' : 'border-border bg-[var(--card-bg)]',
        )}
      >
        <div aria-hidden="true" className="text-2xl mb-2 text-signal">↑</div>
        {file ? (
          <p className="m-0 font-semibold">{file.name}</p>
        ) : (
          <p className="m-0 font-semibold">Drag &amp; drop your CSV here</p>
        )}
        <p className="mt-1 text-sm text-text-tertiary">
          {file ? 'Click to choose a different file' : 'or click to browse'}
        </p>
      </div>
      {uploading && <p data-testid="wizard-uploading" className="text-sm text-text-tertiary">Uploading {file?.name}…</p>}
      {error && <p data-testid="wizard-upload-error" className="text-danger">{error}</p>}
      {columns.length > 0 && (
        <div data-testid="wizard-preview" className="overflow-x-auto border border-border rounded-none">
          <table className="border-collapse text-sm w-full">
            <thead>
              <tr>{columns.map((c) => (
                <th key={c} className="text-left p-2 border-b border-border">
                  {c} <span className="text-text-tertiary font-normal">({types[c]})</span>
                </th>
              ))}</tr>
            </thead>
            <tbody>
              {previewRows.slice(0, 5).map((r, i) => (
                <tr key={i}>{columns.map((_, j) => <td key={j} className="p-2 border-b border-border">{r[j]}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
