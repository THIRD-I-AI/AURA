import React, { useState, useRef } from 'react';
import '../styles/design-system.css';
import '../styles/components.css';
import Button from './ui/Button';
import Alert from './ui/Alert';
import Card, { CardBody, CardHeader } from './ui/Card';
import Badge from './ui/Badge';
import { uploadService, type UploadResponse } from '../services/api';

interface FileUploadProps {
  onFileUploaded?: (response: UploadResponse) => void;
  acceptedFormats?: string[];
}

/**
 * Professional File Upload Component
 * Drag & drop enabled with real backend integration and progress tracking
 */
export const FileUpload: React.FC<FileUploadProps> = ({
  onFileUploaded,
  acceptedFormats = ['csv', 'xlsx', 'json', 'parquet', 'txt'],
}) => {
  const [dragActive, setDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [uploadedResponse, setUploadedResponse] = useState<UploadResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(e.type === 'dragenter' || e.type === 'dragover');
  };

  const validateFile = (file: File): boolean => {
    const ext = file.name.split('.').pop()?.toLowerCase();
    if (!ext || !acceptedFormats.includes(ext)) {
      setError(`Invalid file format. Accepted: ${acceptedFormats.join(', ')}`);
      return false;
    }

    const maxSize = 100 * 1024 * 1024; // 100MB
    if (file.size > maxSize) {
      setError('File size exceeds 100MB limit');
      return false;
    }

    return true;
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      const file = files[0];
      if (validateFile(file)) {
        setSelectedFile(file);
        setError(null);
        handleUpload(file);
      }
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.currentTarget.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (validateFile(file)) {
        setSelectedFile(file);
        setError(null);
        handleUpload(file);
      }
    }
  };

  const handleUpload = async (file: File) => {
    setIsUploading(true);
    setUploadProgress(0);
    setError(null);
    setUploadedResponse(null);

    try {
      // Simulate progress for user feedback (real progress requires XHR)
      const progressInterval = setInterval(() => {
        setUploadProgress((prev) => {
          if (prev >= 90) {
            clearInterval(progressInterval);
            return 90;
          }
          return prev + 10;
        });
      }, 200);

      // Upload file to backend
      const response = await uploadService.uploadFile(file);

      clearInterval(progressInterval);
      setUploadProgress(100);
      setUploadedResponse(response);

      // Notify parent component
      onFileUploaded?.(response);

      setTimeout(() => setUploadProgress(0), 1000);
    } catch (err) {
      // Enhanced error handling to distinguish timeout from other errors
      let errorMessage = 'Upload failed. Please ensure backend services are running.';
      
      if (err instanceof Error) {
        errorMessage = err.message;
        
        // Handle timeout specifically
        if (err.message.includes('timeout') || err.message.includes('took too long')) {
          errorMessage = '⏱️ Upload Timeout: The server took too long to process the file. Try with a smaller file or check server logs.';
        }
        // Handle abort/cancellation
        else if (err.message === 'Upload canceled') {
          errorMessage = 'Upload was canceled by the user.';
        }
        // Handle network errors
        else if (err.message.includes('Network error')) {
          errorMessage = '🌐 Network Error: Unable to reach the server. Make sure backend services are running on port 8000.';
        }
      }
      
      setError(errorMessage);
      setSelectedFile(null);
    } finally {
      setIsUploading(false);
    }
  };

  const handleRemoveFile = () => {
    setSelectedFile(null);
    setUploadedResponse(null);
    setUploadProgress(0);
    setError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const getFileIcon = (filename: string): string => {
    const ext = filename.split('.').pop()?.toLowerCase();
    const icons: Record<string, string> = {
      csv: '📊',
      xlsx: '📈',
      xls: '📈',
      json: '🔗',
      parquet: '🗃️',
      txt: '📄',
    };
    return icons[ext || 'txt'] || '📁';
  };

  return (
    <Card>
      <CardHeader title="Upload File" subtitle="Upload your data file to get started" />

      <CardBody>
        {/* Error Alert */}
        {error && (
          <Alert
            type="error"
            title="Upload Error"
            message={error}
            onClose={() => setError(null)}
          />
        )}

        {!selectedFile ? (
          <>
            {/* Drag & Drop Zone */}
            <div
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              style={{
                padding: 'var(--space-12)',
                border: `2px dashed ${dragActive ? 'var(--color-primary-500)' : 'var(--border-default)'}`,
                borderRadius: 'var(--radius-lg)',
                backgroundColor: dragActive ? 'var(--color-primary-50)' : 'var(--bg-secondary)',
                cursor: 'pointer',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 'var(--space-4)',
                textAlign: 'center',
                transition: 'all var(--transition-base)',
              }}
            >
              <div style={{ fontSize: '3rem' }}>📤</div>
              <div>
                <h3
                  style={{
                    margin: 0,
                    fontSize: 'var(--font-lg)',
                    fontWeight: 'var(--weight-semibold)',
                    color: 'var(--text-primary)',
                  }}
                >
                  Drag and drop your file here
                </h3>
                <p
                  style={{
                    margin: 'var(--space-2) 0 0 0',
                    color: 'var(--text-tertiary)',
                    fontSize: 'var(--font-sm)',
                  }}
                >
                  or click to browse
                </p>
              </div>

              {/* Supported Formats */}
              <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', justifyContent: 'center' }}>
                {acceptedFormats.map((format) => (
                  <Badge key={format} color="primary">
                    .{format.toUpperCase()}
                  </Badge>
                ))}
              </div>

              {/* File Size Limit */}
              <p style={{ margin: 0, fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
                Maximum file size: 100MB
              </p>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              hidden
              accept={acceptedFormats.map((f) => `.${f}`).join(',')}
              onChange={handleFileSelect}
            />
          </>
        ) : (
          <>
            {/* File Preview */}
            <div
              style={{
                padding: 'var(--space-6)',
                backgroundColor: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-lg)',
                border: '1px solid var(--border-default)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)' }}>
                <div
                  style={{
                    fontSize: '2.5rem',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  {getFileIcon(selectedFile.name)}
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <h4
                    style={{
                      margin: 0,
                      fontSize: 'var(--font-base)',
                      fontWeight: 'var(--weight-semibold)',
                      color: 'var(--text-primary)',
                      wordBreak: 'break-word',
                    }}
                  >
                    {selectedFile.name}
                  </h4>
                  <div style={{ display: 'flex', gap: 'var(--space-4)', marginTop: 'var(--space-2)' }}>
                    <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
                      {formatFileSize(selectedFile.size)}
                    </span>
                    <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
                      {selectedFile.type || 'Unknown type'}
                    </span>
                  </div>
                </div>

                {isUploading && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                    <span className="spinner" />
                    <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-secondary)' }}>
                      {uploadProgress}%
                    </span>
                  </div>
                )}

                {uploadedResponse && (
                  <Badge color="success">✓ Uploaded</Badge>
                )}
              </div>

              {/* Progress Bar */}
              {isUploading && (
                <div
                  style={{
                    marginTop: 'var(--space-4)',
                    height: '0.5rem',
                    backgroundColor: 'var(--bg-tertiary)',
                    borderRadius: 'var(--radius-full)',
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      height: '100%',
                      backgroundColor: 'var(--color-primary-500)',
                      width: `${uploadProgress}%`,
                      transition: 'width var(--transition-base)',
                    }}
                  />
                </div>
              )}

              {/* Upload Results */}
              {uploadedResponse && (
                <div
                  style={{
                    marginTop: 'var(--space-4)',
                    padding: 'var(--space-4)',
                    backgroundColor: 'var(--color-success-50)',
                    borderRadius: 'var(--radius-md)',
                    border: '1px solid var(--color-success-200)',
                  }}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', fontSize: 'var(--font-sm)' }}>
                    <div><strong>Rows:</strong> {uploadedResponse.rows.toLocaleString()}</div>
                    <div><strong>Columns:</strong> {uploadedResponse.columns.join(', ')}</div>
                    <div style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', marginTop: 'var(--space-2)' }}>
                      File ID: {uploadedResponse.file_id}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Actions */}
            <div style={{ display: 'flex', gap: 'var(--space-3)', marginTop: 'var(--space-4)' }}>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleRemoveFile}
                disabled={isUploading}
              >
                Remove
              </Button>
              {uploadedResponse && (
                <Button variant="ghost" size="sm" disabled>
                  ✓ Ready for analysis
                </Button>
              )}
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
};

export default FileUpload;
