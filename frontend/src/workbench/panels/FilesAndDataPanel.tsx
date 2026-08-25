/* Files & Data — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md):
   ui-kit primitives + token utilities, no inline styles. Lists real uploaded
   datasets (GET /files) and uploads new ones via uploadService. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Upload } from 'lucide-react';

import { Button } from '@/components/ui-kit/button';
import { DataTable, type ColumnDef } from '@/components/ui-kit/data-table';
import { cn } from '@/lib/cn';
import { uploadService } from '../../services/api';

type Dataset = { filename: string; size: number; modified: string | null };

function fmtSize(bytes: number): string {
  if (!bytes || bytes < 0) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let n = bytes, i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i += 1; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

function fmtModified(modified: string | null): string {
  if (!modified) return '—';
  const d = new Date(modified);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString();
}

function extTag(name: string): { label: string; dot: string; text: string } {
  const ext = name.split('.').pop()?.toLowerCase();
  if (ext === 'csv' || ext === 'xlsx' || ext === 'xls') return { label: (ext || 'csv').toUpperCase(), dot: 'bg-signal', text: 'text-signal' };
  if (ext === 'json') return { label: 'JSON', dot: 'bg-warn', text: 'text-warn' };
  if (ext === 'parquet') return { label: 'PARQUET', dot: 'bg-info', text: 'text-info' };
  return { label: (ext || 'FILE').toUpperCase(), dot: 'bg-text-tertiary', text: 'text-text-tertiary' };
}

const columns: ColumnDef<Dataset>[] = [
  {
    key: 'dataset',
    header: 'Dataset',
    accessor: (f) => {
      const tag = extTag(f.filename);
      return (
        <span className="flex min-w-0 items-center gap-2.5">
          <span className={cn('size-1.5 shrink-0', tag.dot)} />
          <span className="truncate text-sm text-card-foreground">{f.filename}</span>
        </span>
      );
    },
    sortable: true,
    sortValue: (f) => f.filename,
    filterValue: (f) => f.filename,
  },
  {
    key: 'modified',
    header: 'Modified',
    accessor: (f) => <span className="text-text-secondary">{fmtModified(f.modified)}</span>,
    sortable: true,
    sortValue: (f) => f.modified ?? '',
    className: 'w-32',
  },
  {
    key: 'size',
    header: 'Size',
    accessor: (f) => fmtSize(f.size),
    sortable: true,
    sortValue: (f) => f.size,
    align: 'right',
    className: 'w-24',
  },
  {
    key: 'type',
    header: 'Type',
    accessor: (f) => {
      const tag = extTag(f.filename);
      return <span className={cn('font-mono text-2xs font-semibold tracking-wide', tag.text)}>{tag.label}</span>;
    },
    align: 'right',
    className: 'w-24',
  },
];

export default function FilesAndDataPanel() {
  const [files, setFiles] = useState<Dataset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setFiles((await uploadService.getUploadedFiles()) as Dataset[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list datasets.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onFile = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setNotice(null);
    try {
      await uploadService.uploadFile(file);
      setNotice(`Uploaded ${file.name}`);
      await load();
    } catch {
      setError(`Upload failed for ${file.name}.`);
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  }, [load]);

  const count = files?.length ?? 0;
  const totalBytes = (files ?? []).reduce((s, f) => s + (f.size || 0), 0);

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-files-panel">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {files === null ? 'loading…' : `${count} dataset${count === 1 ? '' : 's'} · ${fmtSize(totalBytes)} · workspace uploads`}
        </span>
        <div className="flex-1" />
        <input ref={inputRef} type="file" accept=".csv,.xlsx,.xls,.json,.parquet" onChange={onFile} className="hidden" data-testid="wb-files-input" />
        <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()} disabled={uploading} data-testid="wb-files-upload">
          <Upload /> {uploading ? 'Uploading…' : 'Upload dataset'}
        </Button>
      </div>

      {notice && <div className="border border-signal/40 bg-secondary px-3 py-1.5 font-mono text-xs text-signal">{notice}</div>}
      {error && files !== null && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-destructive">{error}</div>}

      <DataTable
        columns={columns}
        rows={files}
        error={error}
        onRetry={load}
        errorTitle="Unavailable"
        emptyTitle="No datasets yet"
        emptyDescription="Upload a CSV, Excel, JSON, or Parquet file — it becomes queryable in Ask AURA immediately."
        filterPlaceholder="Filter datasets…"
        getRowKey={(f) => f.filename}
      />

      <p className="font-mono text-2xs text-text-tertiary">
        Datasets are workspace-scoped and queryable from Ask AURA — no classic app required.
      </p>
    </div>
  );
}
