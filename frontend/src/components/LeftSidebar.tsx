import React, { useState } from 'react';
import { useTheme } from '../contexts/ThemeContext';
import ConnectionsPanel from './ConnectionsPanel';
import FileUpload from './FileUpload';
import './LeftSidebar.css';

interface Connection {
  id: string;
  name: string;
  type: string;
  status: 'connected' | 'disconnected' | 'connecting';
  host?: string;
  database?: string;
  lastConnected?: Date;
}

interface LeftSidebarProps {
  connections: Connection[];
  onConnectionSelect: (connection: Connection) => void;
  onNewConnection: () => void;
  onFileUpload: (file: File, data: any) => void;
  isLoading?: boolean;
}

const LeftSidebar: React.FC<LeftSidebarProps> = ({
  connections,
  onConnectionSelect,
  onNewConnection,
  onFileUpload,
  isLoading = false,
}) => {
  const { theme } = useTheme();
  const [activeTab, setActiveTab] = useState<'connections' | 'upload'>('connections');

  return (
    <div className="left-sidebar" data-theme={theme}>
      <div className="sidebar-tabs">
        <button
          className={`sidebar-tab ${activeTab === 'connections' ? 'active' : ''}`}
          onClick={() => setActiveTab('connections')}
        >
          <span className="tab-icon">🔌</span>
          <span className="tab-label">Connections</span>
        </button>
        <button
          className={`sidebar-tab ${activeTab === 'upload' ? 'active' : ''}`}
          onClick={() => setActiveTab('upload')}
        >
          <span className="tab-icon">📁</span>
          <span className="tab-label">Files</span>
        </button>
      </div>

      <div className="sidebar-content">
        {activeTab === 'connections' && (
          <ConnectionsPanel
            connections={connections}
            onConnectionSelect={onConnectionSelect}
            onNewConnection={onNewConnection}
          />
        )}
        
        {activeTab === 'upload' && (
          <div className="upload-section">
            <div className="upload-header">
              <h4>📂 Data Files</h4>
              <p>Upload CSV, JSON, Excel files for analysis</p>
            </div>
            <FileUpload
              onFileUpload={onFileUpload}
              isLoading={isLoading}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default LeftSidebar;