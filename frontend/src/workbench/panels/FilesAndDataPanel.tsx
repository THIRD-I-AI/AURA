/* Files & Data — native terminal-authority panel (replaces the embedded
   classic FilesAndData page). Lists the real uploaded datasets from the
   gateway (GET /files via uploadService) and uploads new ones, styled to
   match the Cockpit: dark, mono-first, sharp-cornered, green-signal.
   Behaviour is identical to the classic page — only the presentation is new. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { uploadService } from '../../services/api';

type Dataset = { filename: string; size: number; modified: string | null };

function fmtSize(bytes: number): string {
  if (!bytes || bytes < 0) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let n = bytes, i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i += 1; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

function extTag(name: string): { label: string; color: string } {
  const ext = name.split('.').pop()?.toLowerCase();
  if (ext === 'csv' || ext === 'xlsx' || ext === 'xls') return { label: (ext || 'csv').toUpperCase(), color: 'var(--accent)' };
  if (ext === 'json') return { label: 'JSON', color: 'var(--warn)' };
  if (ext === 'parquet') return { label: 'PARQUET', color: '#7aa2f7' };
  return { label: (ext || 'FILE').toUpperCase(), color: 'var(--text3)' };
}

export default function FilesAndDataPanel() {
  const [files, setFiles] = useState<Dataset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const list = await uploadService.getUploadedFiles();
      setFiles(list as Dataset[]);
      setError(null);
    } catch {
      setError('Could not reach the gateway to list datasets.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onPick = () => inputRef.current?.click();

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
    <div data-testid="wb-files-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span className="aw-mono" style={{ fontSize: 11, color: 'var(--text3)' }}>
          {files === null ? 'loading…' : `${count} dataset${count === 1 ? '' : 's'} · ${fmtSize(totalBytes)} · workspace uploads`}
        </span>
        <div style={{ flex: 1 }} />
        <input ref={inputRef} type="file" accept=".csv,.xlsx,.xls,.json,.parquet" onChange={onFile} style={{ display: 'none' }} data-testid="wb-files-input" />
        <button
          onClick={onPick}
          disabled={uploading}
          className="aw-mono aw-hover-accent-bd"
          data-testid="wb-files-upload"
          style={{
            cursor: uploading ? 'default' : 'pointer', fontSize: 11, fontWeight: 600, letterSpacing: '.04em',
            color: uploading ? 'var(--text3)' : 'var(--accent)', background: 'var(--sunken)',
            border: '1px solid var(--accent-bd)', borderRadius: 0, padding: '7px 14px',
          }}
        >
          {uploading ? 'UPLOADING…' : '↑ UPLOAD DATASET'}
        </button>
      </div>

      {notice && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--accent)', background: 'var(--sunken)', border: '1px solid var(--accent-bd)', padding: '6px 12px' }}>{notice}</div>}
      {error && <div className="aw-mono" style={{ fontSize: 11, color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', padding: '6px 12px' }}>{error}</div>}

      {/* dataset table */}
      <div className="aw-panel" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 96px 84px', gap: 0, padding: '10px 16px', borderBottom: '1px solid var(--hair)' }}>
          {['DATASET', 'SIZE', 'TYPE'].map((h, i) => (
            <div key={h} className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.12em', color: 'var(--text3)', textAlign: i === 0 ? 'left' : 'right' }}>{h}</div>
          ))}
        </div>

        {files === null && (
          <div style={{ padding: '14px 16px', fontSize: 11.5, color: 'var(--text3)' }}>Loading datasets…</div>
        )}
        {files !== null && count === 0 && !error && (
          <div style={{ padding: '22px 16px', fontSize: 12, color: 'var(--text3)', textAlign: 'center', lineHeight: 1.7 }}>
            No datasets yet.<br />Upload a CSV, Excel, JSON, or Parquet file — it becomes queryable in Ask AURA immediately.
          </div>
        )}
        {(files ?? []).map((f) => {
          const tag = extTag(f.filename);
          return (
            <div key={f.filename} className="aw-nav-item" style={{ display: 'grid', gridTemplateColumns: '1fr 96px 84px', alignItems: 'center', gap: 0, padding: '9px 16px', borderTop: '1px solid var(--hair)', cursor: 'default' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                <span style={{ width: 6, height: 6, flex: 'none', background: tag.color, borderRadius: 0 }} />
                <span style={{ fontSize: 12.5, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.filename}</span>
              </div>
              <div className="aw-mono" style={{ fontSize: 11, color: 'var(--text2)', textAlign: 'right' }}>{fmtSize(f.size)}</div>
              <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.06em', color: tag.color, textAlign: 'right' }}>{tag.label}</div>
            </div>
          );
        })}
      </div>

      <div className="aw-mono" style={{ fontSize: 10, color: 'var(--text3)' }}>
        Datasets are workspace-scoped and queryable from Ask AURA — no classic app required.
      </div>
    </div>
  );
}
