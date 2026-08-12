/* Files & Data — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md):
   ui-kit primitives + token utilities, no inline styles. Lists real uploaded
   datasets (GET /files) and uploads new ones via uploadService. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Upload } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
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

function extTag(name: string): { label: string; dot: string; text: string } {
  const ext = name.split('.').pop()?.toLowerCase();
  if (ext === 'csv' || ext === 'xlsx' || ext === 'xls') return { label: (ext || 'csv').toUpperCase(), dot: 'bg-signal', text: 'text-signal' };
  if (ext === 'json') return { label: 'JSON', dot: 'bg-warn', text: 'text-warn' };
  if (ext === 'parquet') return { label: 'PARQUET', dot: 'bg-info', text: 'text-info' };
  return { label: (ext || 'FILE').toUpperCase(), dot: 'bg-text-tertiary', text: 'text-text-tertiary' };
}

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
      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-destructive">{error}</div>}

      <Panel>
        <div className="grid grid-cols-[1fr_96px_84px] border-b border-border px-4 py-2.5 font-mono text-2xs font-semibold uppercase tracking-wider text-text-tertiary">
          <span>Dataset</span><span className="text-right">Size</span><span className="text-right">Type</span>
        </div>
        {files === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading datasets…</div>}
        {files !== null && count === 0 && !error && (
          <EmptyState intent="empty" title="No datasets yet" description="Upload a CSV, Excel, JSON, or Parquet file — it becomes queryable in Ask AURA immediately." />
        )}
        {(files ?? []).map((f) => {
          const tag = extTag(f.filename);
          return (
            <div key={f.filename} className="grid grid-cols-[1fr_96px_84px] items-center border-t border-border px-4 py-2.5 transition-colors hover:bg-accent">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className={cn('size-1.5 shrink-0', tag.dot)} />
                <span className="truncate text-sm text-card-foreground">{f.filename}</span>
              </div>
              <div className="text-right font-mono text-xs text-text-secondary">{fmtSize(f.size)}</div>
              <div className={cn('text-right font-mono text-2xs font-semibold tracking-wide', tag.text)}>{tag.label}</div>
            </div>
          );
        })}
      </Panel>

      <p className="font-mono text-2xs text-text-tertiary">
        Datasets are workspace-scoped and queryable from Ask AURA — no classic app required.
      </p>
    </div>
  );
}
