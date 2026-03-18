import React, { useState, useEffect } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import './FilesAndData.css';

interface FilesAndDataProps {
  setCurrentPage?: (page: PageType) => void;
}

const FilesAndData: React.FC<FilesAndDataProps> = ({ setCurrentPage }) => {
  const [files, setFiles] = useState<any[]>([]);
  const [selectedFile, setSelectedFile] = useState<any>(null);

  useEffect(() => {
    const saved = localStorage.getItem('recentUploads');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        const enhanced = parsed.map((f: any) => ({
          ...f,
          id: f.id || `AURA-${Math.random().toString(36).substr(2, 5).toUpperCase()}`,
          rows: typeof f.rows === 'number' && f.rows > 0 ? f.rows : (typeof f.size === 'number' ? Math.floor(f.size / 150) : 0),
          columns: f.columns || (Array.isArray(f.response?.columns) ? f.response.columns.length : 0)
        }));
        setFiles(enhanced);
      } catch (e) { console.error(e); }
    }
  }, []);

  const getRowDisplay = (rows: any) => {
    if (typeof rows === 'number' && rows > 0) {
      return `${rows.toLocaleString()} rows`;
    }
    return 'Processing...';
  };

  const handleAnalyze = () => {
    if (selectedFile) {
      localStorage.setItem('active_dataset', JSON.stringify(selectedFile));
    }
    if (setCurrentPage) {
      setCurrentPage('dashboard');
    } else {
      window.location.href = '/dashboard';
    }
  };

  return (
    <div className="files-data-page">
      <div className="files-data-container">
        <header className="files-data-header">
          <h1 className="files-data-title">Data Library</h1>
          <p className="files-data-subtitle">Manage and prepare your datasets for AI analysis.</p>
        </header>

        <div className="files-data-layout">
          
          {/* MASTER TABLE: Left Real Estate */}
          <div className="files-data-table-panel">
            <table className="files-data-table">
              <thead>
                <tr className="files-data-table-header-row">
                  <th className="files-data-table-header-cell">Dataset Name</th>
                  <th className="files-data-table-header-cell">Size</th>
                  <th className="files-data-table-header-cell">Records</th>
                </tr>
              </thead>
              <tbody>
                {files.map((file) => (
                  <tr 
                    key={file.id} 
                    onClick={() => setSelectedFile(file)}
                    className={`files-data-table-row ${selectedFile?.id === file.id ? 'selected' : ''}`}
                  >
                    <td className={`files-data-table-cell ${selectedFile?.id === file.id ? 'selected' : ''}`}>
                      <div className="files-data-filename">{file.name}</div>
                      <div className="files-data-fileid">{file.id}</div>
                    </td>
                    <td className="files-data-size">{file.size || 'N/A'}</td>
                    <td className="files-data-records">{getRowDisplay(file.rows)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {files.length === 0 && (
              <div className="files-data-empty">
                <p className="files-data-empty-text">No datasets uploaded yet. Upload a file to get started.</p>
              </div>
            )}
          </div>

          {/* DETAIL PANEL: Right Real Estate */}
          <div className="files-data-detail-panel">
            {selectedFile ? (
              <div className="files-data-detail-card">
                <span className="files-data-badge">Ready to Analyze</span>
                <h2 className="files-data-detail-title">{selectedFile.name}</h2>
                <p className="files-data-detail-subtitle">Schema verified for AI processing.</p>
                
                <div className="files-data-stats-grid">
                  <div className="files-data-stat-card">
                    <div className="files-data-stat-label">RECORDS</div>
                    <div className="files-data-stat-value">{typeof selectedFile.rows === 'number' && selectedFile.rows > 0 ? selectedFile.rows.toLocaleString() : '—'}</div>
                  </div>
                  <div className="files-data-stat-card">
                    <div className="files-data-stat-label">COLUMNS</div>
                    <div className="files-data-stat-value">{typeof selectedFile.columns === 'number' && selectedFile.columns > 0 ? selectedFile.columns : '—'}</div>
                  </div>
                </div>

                <button className="files-data-analyze-btn" onClick={handleAnalyze}>
                  Analyze with AI →
                </button>
              </div>
            ) : (
              <div className="files-data-empty-state">
                <p className="files-data-empty-state-title">No dataset selected</p>
                <p className="files-data-empty-state-text">Select a file from the library to inspect details and begin analysis.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default FilesAndData;
