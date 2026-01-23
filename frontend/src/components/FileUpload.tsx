import React, { useState, useRef, useCallback } from 'react';
import { useTheme } from '../contexts/ThemeContext';
import './FileUpload.css';

interface FileUploadProps {
  onFileUpload: (file: File, data: any) => void;
  isLoading?: boolean;
}

interface UploadedFile {
  file: File;
  preview: any[];
  summary: {
    rows: number;
    columns: number;
    fileSize: string;
    fileType: string;
  };
}

const FileUpload: React.FC<FileUploadProps> = ({ onFileUpload }) => {
  const { theme } = useTheme();
  const [dragActive, setDragActive] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<UploadedFile | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const supportedTypes = [
    { type: 'text/csv', ext: '.csv', icon: '📊' },
    { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', ext: '.xlsx', icon: '📈' },
    { type: 'application/vnd.ms-excel', ext: '.xls', icon: '📈' },
    { type: 'application/json', ext: '.json', icon: '🔗' },
    { type: 'text/plain', ext: '.txt', icon: '📄' },
    { type: 'application/octet-stream', ext: '.parquet', icon: '🗃️' },
    { type: 'application/x-parquet', ext: '.parquet', icon: '🗃️' }
  ];

  // Get unique format displays (remove duplicates for UI)
  const uniqueFormats = supportedTypes.reduce((acc, curr) => {
    const existing = acc.find(item => item.ext === curr.ext);
    if (!existing) {
      acc.push(curr);
    }
    return acc;
  }, [] as typeof supportedTypes);

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  // File parsing is now handled on the backend

  const handleFile = useCallback(async (file: File) => {
    if (!supportedTypes.some(type => type.type === file.type || file.name.endsWith(type.ext))) {
      alert('Unsupported file type. Please upload CSV, Excel, JSON, TXT, or Parquet files.');
      return;
    }

    setIsProcessing(true);
    
    try {
      // Create FormData for file upload
      const formData = new FormData();
      formData.append('file', file);
      
      // Upload to backend
      const response = await fetch('http://localhost:8000/files/upload', {
        method: 'POST',
        body: formData,
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Upload failed');
      }
      
      const result = await response.json();
      
      // Create uploaded file info from backend response
      const uploadedFileInfo: UploadedFile = {
        file,
        preview: result.preview || [],
        summary: {
          rows: result.file_info.rows_count || 0,
          columns: result.file_info.columns_count || 0,
          fileSize: formatFileSize(result.file_info.file_size),
          fileType: file.type || 'Unknown'
        }
      };

      setUploadedFile(uploadedFileInfo);
      
      // Call the onFileUpload callback with processed data
      onFileUpload(file, result.preview || []);
      
      console.log('File uploaded successfully:', result);
      
    } catch (error) {
      console.error('Error uploading file:', error);
      alert(`Error uploading file: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsProcessing(false);
    }
  }, [onFileUpload]);

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFile(e.dataTransfer.files[0]);
    }
  }, [handleFile]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      handleFile(e.target.files[0]);
    }
  };

  const openFileDialog = () => {
    fileInputRef.current?.click();
  };

  const generateSampleData = () => {
    const sampleData = [
      { product: 'Laptop', sales: 1200, revenue: 960000, quarter: 'Q1' },
      { product: 'Mobile', sales: 2300, revenue: 690000, quarter: 'Q1' },
      { product: 'Tablet', sales: 800, revenue: 320000, quarter: 'Q1' },
      { product: 'Laptop', sales: 1500, revenue: 1200000, quarter: 'Q2' },
      { product: 'Mobile', sales: 2800, revenue: 840000, quarter: 'Q2' },
      { product: 'Tablet', sales: 950, revenue: 380000, quarter: 'Q2' }
    ];

    const blob = new Blob([JSON.stringify(sampleData, null, 2)], { type: 'application/json' });
    const file = new File([blob], 'sample_sales_data.json', { type: 'application/json' });
    handleFile(file);
  };

  return (
    <div className="file-upload-container" data-theme={theme}>
      <div 
        className={`file-upload-zone ${dragActive ? 'drag-active' : ''} ${isProcessing ? 'processing' : ''}`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        onClick={openFileDialog}
      >
        <input
          ref={fileInputRef}
          type="file"
          onChange={handleFileSelect}
          accept=".csv,.xlsx,.xls,.json,.txt,.parquet"
          className="hidden-input"
          aria-label="File upload input"
        />
        
        {isProcessing ? (
          <div className="upload-processing">
            <div className="processing-spinner"></div>
            <h3>Processing File...</h3>
            <p>Analyzing your data</p>
          </div>
        ) : (
          <div className="upload-content">
            <div className="upload-icon">📂</div>
            <h3>Drop your data file here</h3>
            <p>or <span className="upload-link">click to browse</span></p>
            
            <div className="supported-formats">
              <span>Supported formats:</span>
              <div className="format-list">
                {uniqueFormats.map((type, index) => (
                  <span key={index} className="format-item">
                    {type.icon} {type.ext}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="upload-actions">
        <button className="sample-data-btn" onClick={generateSampleData}>
          ✨ Try Sample Data
        </button>
      </div>

      {uploadedFile && (
        <div className="file-preview">
          <div className="file-info">
            <div className="file-header">
              <h4>📊 {uploadedFile.file?.name || 'Unknown file'}</h4>
              <div className="file-stats">
                <span className="stat">📏 {uploadedFile.summary?.rows || 0} rows</span>
                <span className="stat">📋 {uploadedFile.summary?.columns || 0} columns</span>
                <span className="stat">💾 {uploadedFile.summary?.fileSize || 'Unknown size'}</span>
              </div>
            </div>
            
            {uploadedFile.preview.length > 0 && (
              <div className="data-preview">
                <h5>Data Preview:</h5>
                <div className="preview-table">
                  <table>
                    <thead>
                      <tr>
                        {uploadedFile.preview[0] && Object.keys(uploadedFile.preview[0]).map(key => (
                          <th key={key}>{key}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {uploadedFile.preview.map((row, index) => (
                        <tr key={index}>
                          {Object.values(row).map((value: any, i) => (
                            <td key={i}>{String(value)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default FileUpload;