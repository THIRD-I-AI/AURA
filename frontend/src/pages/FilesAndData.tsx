import React, { useState, useEffect } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import { type PageType } from '../components/Layout/AppLayout';
import { useAuraStore, type UploadedFile } from '../store';
import './FilesAndData.css';

interface FilesAndDataProps {
  setCurrentPage?: (page: PageType) => void;
}

// ── SVG Icons ────────────────────────────────────────────────────────────────

const FileSpreadsheetIcon = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
    <polyline points="14 2 14 8 20 8"/>
    <line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/>
    <line x1="8" y1="9" x2="10" y2="9"/>
  </svg>
);

const FileJsonIcon = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
    <polyline points="14 2 14 8 20 8"/>
    <path d="M10 12a1 1 0 0 0-1 1v1a1 1 0 0 1-1 1 1 1 0 0 1 1 1v1a1 1 0 0 0 1 1"/>
    <path d="M14 18a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1 1 1 0 0 1-1-1v-1a1 1 0 0 0-1-1"/>
  </svg>
);

const DatabaseIcon = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <ellipse cx="12" cy="5" rx="9" ry="3"/>
    <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
    <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
  </svg>
);

const FileTextIcon = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
    <polyline points="14 2 14 8 20 8"/>
    <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
    <polyline points="10 9 9 9 8 9"/>
  </svg>
);

const UploadIcon = ({ size = 28 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
    <polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
  </svg>
);

const AnalyzeIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
  </svg>
);

// ── KPI card styles ────────────────────────────────────────────────────────────

const kpiCardStyle: React.CSSProperties = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-default)',
  borderRadius: 'var(--radius-lg)',
  padding: 'var(--space-4) var(--space-5)',
  display: 'flex',
  flexDirection: 'column',
  gap: 'var(--space-1)',
};

const kpiLabelStyle: React.CSSProperties = {
  fontSize: '10px',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.07em',
  color: 'var(--text-tertiary)',
};

const kpiValueStyle: React.CSSProperties = {
  fontSize: 'var(--font-xl)',
  fontWeight: 700,
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-primary)',
  lineHeight: 1.1,
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function getFileIcon(name: string, size = 16) {
  const ext = name.split('.').pop()?.toLowerCase();
  if (ext === 'csv' || ext === 'xlsx' || ext === 'xls') return <FileSpreadsheetIcon size={size} />;
  if (ext === 'json') return <FileJsonIcon size={size} />;
  if (ext === 'parquet') return <DatabaseIcon size={size} />;
  return <FileTextIcon size={size} />;
}

function getIconColor(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase();
  if (ext === 'csv' || ext === 'xlsx' || ext === 'xls') return '#34d399';
  if (ext === 'json') return '#fbbf24';
  if (ext === 'parquet') return '#a78bfa';
  return '#60a5fa';
}

function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

// ─────────────────────────────────────────────────────────────────────────────

const FilesAndData: React.FC<FilesAndDataProps> = ({ setCurrentPage }) => {
  const { state: { files }, actions: { loadFilesFromStorage } } = useAuraStore();
  const [selectedFile, setSelectedFile] = useState<UploadedFile | null>(null);

  useEffect(() => { loadFilesFromStorage(); }, []);

  // Keep selection in sync when files list updates
  useEffect(() => {
    if (selectedFile) {
      const refreshed = files.find(f => f.id === selectedFile.id);
      if (refreshed) setSelectedFile(refreshed);
    }
  }, [files]);

  const readyCount  = files.filter(f => f.status === 'ready').length;
  const totalRows   = files.reduce((s, f) => s + (f.rows || 0), 0);
  const totalSize   = files.reduce((s, f) => s + (f.sizeBytes || 0), 0);

  const handleAnalyze = () => {
    if (selectedFile) localStorage.setItem('active_dataset', JSON.stringify(selectedFile));
    setCurrentPage?.('dashboard');
  };

  // Bar chart: top-8 files by size
  const chartData = [...files]
    .sort((a, b) => (b.sizeBytes || 0) - (a.sizeBytes || 0))
    .slice(0, 8)
    .map(f => ({
      name: f.name.length > 12 ? f.name.slice(0, 10) + '…' : f.name,
      fullName: f.name,
      size: Math.round((f.sizeBytes || 0) / 1024),
    }));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', height: '100%', minHeight: 0 }}>

      {/* ── KPI bar ────────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 'var(--space-3)', flexShrink: 0 }}>
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>Datasets</span>
          <span style={kpiValueStyle}>{files.length}</span>
          <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-disabled)' }}>
            {files.length === 1 ? '1 file loaded' : `${files.length} files loaded`}
          </span>
        </div>
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>Total Rows</span>
          <span style={kpiValueStyle}>{totalRows > 0 ? totalRows.toLocaleString() : '—'}</span>
          <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-disabled)' }}>across all datasets</span>
        </div>
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>Storage</span>
          <span style={kpiValueStyle}>{formatFileSize(totalSize)}</span>
          <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-disabled)' }}>total size</span>
        </div>
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>Ready</span>
          <span style={{ ...kpiValueStyle, color: readyCount > 0 ? '#34d399' : 'var(--text-disabled)' }}>
            {readyCount}<span style={{ fontSize: 'var(--font-base)', color: 'var(--text-tertiary)', fontWeight: 400 }}>/{files.length}</span>
          </span>
          <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-disabled)' }}>for analysis</span>
        </div>
      </div>

      {/* ── Main split ─────────────────────────────────────────────── */}
      <div className="files-data-page" style={{ flex: 1, minHeight: 0 }}>

        {/* ── Left: file list ──────────────────────────────────────── */}
        <div className="files-table-panel">
          <div className="files-table-header">
            <span className="files-table-title">Data Library</span>
            <span style={{
              fontSize: '10px', fontWeight: 700, padding: '2px 8px',
              background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-full)', color: 'var(--text-tertiary)',
            }}>
              {files.length} file{files.length !== 1 ? 's' : ''}
            </span>
          </div>

          <div className="files-table-body">
            {files.length === 0 ? (
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', gap: 'var(--space-4)', padding: 'var(--space-10)',
                color: 'var(--text-disabled)',
              }}>
                <UploadIcon />
                <div style={{ textAlign: 'center' }}>
                  <p style={{ margin: '0 0 var(--space-1)', fontSize: 'var(--font-sm)', color: 'var(--text-secondary)', fontWeight: 500 }}>
                    No datasets yet
                  </p>
                  <p style={{ margin: 0, fontSize: 'var(--font-xs)', color: 'var(--text-disabled)' }}>
                    Upload a CSV, Excel, JSON, or Parquet file to get started
                  </p>
                </div>
                <button
                  onClick={() => setCurrentPage?.('upload')}
                  style={{
                    padding: 'var(--space-2) var(--space-5)', background: 'var(--accent)',
                    border: 'none', borderRadius: 'var(--radius-md)', color: '#fff',
                    fontSize: 'var(--font-sm)', fontWeight: 600, cursor: 'pointer',
                    fontFamily: 'var(--font-sans)',
                  }}
                >
                  Upload a File
                </button>
              </div>
            ) : (
              files.map(file => (
                <div
                  key={file.id}
                  className={`file-row${selectedFile?.id === file.id ? ' file-row--active' : ''}`}
                  onClick={() => setSelectedFile(file)}
                >
                  <div className="file-icon" style={{ color: getIconColor(file.name) }}>
                    {getFileIcon(file.name)}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="file-name" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {file.name}
                    </div>
                    <div className="file-meta">
                      {formatFileSize(file.sizeBytes)}
                      {file.rows > 0 && <> · {file.rows.toLocaleString()} rows</>}
                      {file.columns > 0 && <> · {file.columns} cols</>}
                    </div>
                  </div>
                  <span className={`file-status file-status--${file.status === 'ready' ? 'ready' : 'processing'}`}>
                    {file.status === 'ready' ? 'Ready' : 'Uploaded'}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* ── Right: detail panel ──────────────────────────────────── */}
        <div className="file-detail-panel">
          {selectedFile ? (
            <>
              {/* File header */}
              <div style={{ padding: 'var(--space-4) var(--space-5)', borderBottom: '1px solid var(--border-subtle)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
                  <div style={{
                    width: 38, height: 38, borderRadius: 'var(--radius-md)', flexShrink: 0,
                    background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: getIconColor(selectedFile.name),
                  }}>
                    {getFileIcon(selectedFile.name, 18)}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <p style={{ margin: 0, fontSize: 'var(--font-sm)', fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {selectedFile.name}
                    </p>
                    <p style={{ margin: '2px 0 0', fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
                      Uploaded {formatDate(selectedFile.uploadedAt)} · {selectedFile.id}
                    </p>
                  </div>
                  <span className={`file-status file-status--${selectedFile.status === 'ready' ? 'ready' : 'processing'}`} style={{ flexShrink: 0 }}>
                    {selectedFile.status === 'ready' ? 'Ready' : 'Uploaded'}
                  </span>
                </div>
              </div>

              {/* Stats */}
              <div className="file-stats-grid">
                <div className="file-stat-cell">
                  <div className="file-stat-cell__label">Rows</div>
                  <div className="file-stat-cell__value">
                    {selectedFile.rows > 0 ? selectedFile.rows.toLocaleString() : '—'}
                  </div>
                </div>
                <div className="file-stat-cell">
                  <div className="file-stat-cell__label">Columns</div>
                  <div className="file-stat-cell__value">
                    {selectedFile.columns > 0 ? selectedFile.columns : '—'}
                  </div>
                </div>
                <div className="file-stat-cell">
                  <div className="file-stat-cell__label">Size</div>
                  <div className="file-stat-cell__value">{formatFileSize(selectedFile.sizeBytes)}</div>
                </div>
                <div className="file-stat-cell">
                  <div className="file-stat-cell__label">Status</div>
                  <div className="file-stat-cell__value" style={{ fontSize: 'var(--font-sm)', color: selectedFile.status === 'ready' ? '#34d399' : 'var(--text-secondary)' }}>
                    {selectedFile.status === 'ready' ? 'Ready' : 'Uploaded'}
                  </div>
                </div>
              </div>

              {/* Column chips */}
              {selectedFile.columnNames && selectedFile.columnNames.length > 0 && (
                <div style={{ padding: 'var(--space-3) var(--space-5)', borderBottom: '1px solid var(--border-subtle)', overflow: 'hidden' }}>
                  <p style={{ margin: '0 0 var(--space-2)', fontSize: '10px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                    Schema · {selectedFile.columnNames.length} columns
                  </p>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-1-5)', maxHeight: 96, overflow: 'hidden' }}>
                    {selectedFile.columnNames.map(col => (
                      <span key={col} style={{
                        fontSize: '11px', padding: '2px 8px',
                        background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-full)', color: 'var(--text-secondary)',
                        fontFamily: 'var(--font-mono)',
                      }}>
                        {col}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Size comparison chart */}
              {chartData.length > 1 && (
                <div style={{ padding: 'var(--space-3) var(--space-5)', borderBottom: '1px solid var(--border-subtle)' }}>
                  <p style={{ margin: '0 0 var(--space-2)', fontSize: '10px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                    Dataset Sizes (KB)
                  </p>
                  <ResponsiveContainer width="100%" height={80}>
                    <BarChart data={chartData} barCategoryGap="25%" margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
                      <XAxis dataKey="name" tick={{ fontSize: 9, fill: 'var(--text-disabled)' }} axisLine={false} tickLine={false} />
                      <YAxis hide />
                      <Tooltip
                        contentStyle={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', fontSize: 11, color: 'var(--text-primary)' }}
                        formatter={(v: number) => [`${v} KB`, 'Size']}
                        labelFormatter={(_: unknown, payload: { payload?: { fullName?: string } }[]) => payload?.[0]?.payload?.fullName ?? ''}
                      />
                      <Bar dataKey="size" radius={[3, 3, 0, 0]}>
                        {chartData.map((entry, i) => (
                          <Cell
                            key={i}
                            fill={entry.fullName === selectedFile.name ? 'var(--accent)' : 'var(--bg-elevated)'}
                            stroke={entry.fullName === selectedFile.name ? 'var(--accent)' : 'var(--border-default)'}
                            strokeWidth={1}
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}

              {/* Analyze button */}
              <div style={{ padding: 'var(--space-4) var(--space-5)', marginTop: 'auto' }}>
                <button
                  onClick={handleAnalyze}
                  style={{
                    width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    gap: 'var(--space-2)', padding: 'var(--space-3)',
                    background: 'var(--accent)', border: 'none', borderRadius: 'var(--radius-md)',
                    color: '#fff', fontWeight: 600, fontSize: 'var(--font-sm)', cursor: 'pointer',
                    fontFamily: 'var(--font-sans)', transition: 'opacity var(--dur-fast)',
                  }}
                  onMouseOver={e => (e.currentTarget.style.opacity = '0.85')}
                  onMouseOut={e => (e.currentTarget.style.opacity = '1')}
                >
                  <AnalyzeIcon /> Analyze with AI
                </button>
              </div>
            </>
          ) : (
            <div style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
              gap: 'var(--space-3)', height: '100%', padding: 'var(--space-8)',
            }}>
              <div style={{ color: 'var(--text-disabled)' }}>
                <FileTextIcon size={36} />
              </div>
              <div style={{ textAlign: 'center' }}>
                <p style={{ margin: 0, fontSize: 'var(--font-sm)', color: 'var(--text-secondary)', fontWeight: 500 }}>
                  No dataset selected
                </p>
                <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--font-xs)', color: 'var(--text-disabled)' }}>
                  Click a file from the library to inspect its schema and stats
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default FilesAndData;
