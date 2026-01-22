import React, { useState, useEffect } from 'react';
import './DatabaseConnector.css';
import { connectorService, type ConnectionCredentials } from '../services/api';

interface DatabaseType {
  type: string;
  name: string;
  default_port: number;
  supports_ssl: boolean;
  description: string;
}

interface DatabaseConnection {
  id: string;
  name: string;
  type: string;
  host: string;
  port: number;
  database: string;
  username: string;
  ssl_enabled: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  metadata: Record<string, any>;
}

interface TableInfo {
  name: string;
  schema: string;
  columns: Array<{
    name: string;
    type: string;
    nullable: boolean;
    primary_key?: boolean;
  }>;
  row_count?: number;
}

interface SchemaInfo {
  connection_id: string;
  schemas: string[];
  tables: TableInfo[];
  views: TableInfo[];
  last_updated: string;
}

interface DatabaseConnectorProps {
  onConnectionsUpdated?: (connections: DatabaseConnection[]) => void;
}

const DEFAULT_SUPPORTED_DATABASES: DatabaseType[] = [
  {
    type: 'postgresql',
    name: 'PostgreSQL',
    default_port: 5432,
    supports_ssl: true,
    description: 'Advanced open-source relational database'
  },
  {
    type: 'mysql',
    name: 'MySQL',
    default_port: 3306,
    supports_ssl: true,
    description: 'Popular open-source relational database'
  },
  {
    type: 'sqlite',
    name: 'SQLite',
    default_port: 0,
    supports_ssl: false,
    description: 'Lightweight file-based database'
  }
];

const DatabaseConnector: React.FC<DatabaseConnectorProps> = ({ onConnectionsUpdated }) => {
  const [connections, setConnections] = useState<DatabaseConnection[]>([]);
  const [supportedDbs, setSupportedDbs] = useState<DatabaseType[]>(DEFAULT_SUPPORTED_DATABASES);
  const [selectedConnection, setSelectedConnection] = useState<string | null>(null);
  const [schemaInfo, setSchemaInfo] = useState<SchemaInfo | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [formData, setFormData] = useState({
    name: '',
    type: 'postgresql',
    host: '',
    port: 5432,
    database: '',
    username: '',
    password: '',
    ssl_enabled: false
  });

  useEffect(() => {
    loadConnections();
    loadSupportedDatabases();
  }, []);

  useEffect(() => {
    if (!supportedDbs.length) {
      return;
    }

    setFormData((prev) => {
      const hasSelectedType = supportedDbs.some((db) => db.type === prev.type);
      if (hasSelectedType) {
        return prev;
      }

      const fallback = supportedDbs[0];
      if (!fallback) {
        return prev;
      }

      return {
        ...prev,
        type: fallback.type,
        port: fallback.default_port ?? prev.port ?? 5432
      };
    });
  }, [supportedDbs]);

  const loadConnections = async () => {
    try {
      setIsLoading(true);
      const data = await connectorService.listSources();
      setConnections(data as any);
      onConnectionsUpdated?.(data as any);
    } catch (error) {
      console.error('Error loading connections:', error);
      setError('Failed to load connections. Is the backend running?');
    } finally {
      setIsLoading(false);
    }
  };

  const loadSupportedDatabases = async () => {
    try {
      const databases = await connectorService.getSupportedDatabases();
      if (Array.isArray(databases) && databases.length > 0) {
        const mapped = databases.map(type => ({
          type,
          name: type.charAt(0).toUpperCase() + type.slice(1),
          default_port: type === 'postgresql' ? 5432 : type === 'mysql' ? 3306 : 0,
          supports_ssl: type !== 'sqlite',
          description: `${type} database`
        }));
        setSupportedDbs(mapped);
      } else {
        setSupportedDbs(DEFAULT_SUPPORTED_DATABASES);
      }
    } catch (error) {
      console.error('Error loading supported databases:', error);
      setSupportedDbs(DEFAULT_SUPPORTED_DATABASES);
    }
  };

  const handleAddConnection = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError(null);

    try {
      // Cast form data to ConnectionCredentials
      const credentials: ConnectionCredentials = {
        name: formData.name,
        type: formData.type as 'postgresql' | 'mysql' | 'sqlite',
        host: formData.host,
        port: formData.port,
        database: formData.database,
        username: formData.username,
        password: formData.password,
        ssl: formData.ssl_enabled,
      };

      // Call the API service
      const newSource = await connectorService.registerSource(credentials);

      // Success! Refresh connections list
      await loadConnections();

      // Show success notification (could use toast library)
      alert(`✓ Source "${newSource.name}" connected successfully!`);

      // Reset form
      setShowAddForm(false);
      setFormData({
        name: '',
        type: 'postgresql',
        host: '',
        port: 5432,
        database: '',
        username: '',
        password: '',
        ssl_enabled: false
      });
    } catch (error: any) {
      console.error('Error creating database connection', error);
      const errorMessage = error.message || 'Connection failed. Check credentials and network.';
      setError(errorMessage);
      alert(`✗ Connection Failed: ${errorMessage}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleTestConnection = async (connectionId: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await connectorService.testConnection(connectionId);
      alert(result.success ? `✓ ${result.message}` : `✗ ${result.message}`);
    } catch (error: any) {
      console.error('Error testing database connection', error);
      alert(`✗ Connection test failed: ${error.message || 'Unknown error'}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteConnection = async (connectionId: string) => {
    if (!confirm('Are you sure you want to delete this connection?')) return;

    setIsLoading(true);
    try {
      await connectorService.deleteSource(connectionId);
      await loadConnections();
      if (selectedConnection === connectionId) {
        setSelectedConnection(null);
        setSchemaInfo(null);
      }
      alert('✓ Connection deleted successfully');
    } catch (error: any) {
      console.error('Error deleting database connection', error);
      alert(`✗ Failed to delete: ${error.message || 'Unknown error'}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleViewSchema = async (connectionId: string) => {
    setIsLoading(true);
    setSelectedConnection(connectionId);
    
    try {
      const response = await fetch(`http://localhost:8002/connections/${connectionId}/schema`);
      if (response.ok) {
        const schema = await response.json();
        setSchemaInfo(schema);
      } else {
        setError('Failed to load schema');
      }
    } catch (error) {
      console.error('Error loading database schema', error);
      setError('Network error: Unable to load schema');
    } finally {
      setIsLoading(false);
    }
  };

  const handleTypeChange = (type: string) => {
    const dbType = supportedDbs.find((db) => db.type === type);
    setFormData((prev) => ({
      ...prev,
      type,
      port: dbType?.default_port ?? prev.port ?? 5432
    }));
  };

  return (
    <div className="database-connector">
      <div className="connector-header">
        <h2>🗄️ Database Connections</h2>
        <p>Connect to any database and explore your data</p>
        <button 
          className="add-connection-btn"
          onClick={() => setShowAddForm(true)}
        >
          ➕ Add Connection
        </button>
      </div>

      {error && (
        <div className="error-message">
          ❌ {error}
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      <div className="connector-content">
        <div className="connections-panel">
          <h3>Active Connections ({connections.length})</h3>
          
          {connections.length === 0 ? (
            <div className="empty-state">
              <p>No database connections configured</p>
              <p>Add your first connection to get started</p>
            </div>
          ) : (
            <div className="connections-list">
              {connections.map((conn) => (
                <div
                  key={conn.id}
                  className={`connection-card ${selectedConnection === conn.id ? 'selected' : ''}`}
                >
                  <div className="connection-header">
                    <div className="connection-info">
                      <h4>{conn.name}</h4>
                      <span className="connection-type">{conn.type.toUpperCase()}</span>
                      <span className={`connection-status ${conn.is_active ? 'active' : 'inactive'}`}>
                        {conn.is_active ? '🟢 Active' : '🔴 Inactive'}
                      </span>
                    </div>
                    <div className="connection-actions">
                      <button 
                        onClick={() => handleTestConnection(conn.id)}
                        disabled={isLoading}
                        title="Test Connection"
                      >
                        🔍
                      </button>
                      <button 
                        onClick={() => handleViewSchema(conn.id)}
                        disabled={isLoading}
                        title="View Schema"
                      >
                        📊
                      </button>
                      <button 
                        onClick={() => handleDeleteConnection(conn.id)}
                        className="delete-btn"
                        title="Delete Connection"
                      >
                        🗑️
                      </button>
                    </div>
                  </div>
                  
                  <div className="connection-details">
                    <p><strong>Host:</strong> {conn.host}:{conn.port}</p>
                    <p><strong>Database:</strong> {conn.database}</p>
                    <p><strong>Username:</strong> {conn.username}</p>
                    <p><strong>SSL:</strong> {conn.ssl_enabled ? 'Enabled' : 'Disabled'}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {schemaInfo && (
          <div className="schema-panel">
            <h3>📊 Database Schema</h3>
            <div className="schema-info">
              <p><strong>Connection:</strong> {connections.find(c => c.id === selectedConnection)?.name}</p>
              <p><strong>Schemas:</strong> {schemaInfo.schemas.join(', ')}</p>
              <p><strong>Last Updated:</strong> {new Date(schemaInfo.last_updated).toLocaleString()}</p>
            </div>

            <div className="schema-content">
              <div className="tables-section">
                <h4>📋 Tables ({schemaInfo.tables.length})</h4>
                <div className="tables-list">
                  {schemaInfo.tables.map((table) => {
                    const tableKey = `${table.schema}.${table.name}`;
                    return (
                      <div key={tableKey} className="table-card">
                        <div className="table-header">
                          <h5>{table.name}</h5>
                          <span className="table-schema">{table.schema}</span>
                          {table.row_count && (
                            <span className="row-count">{table.row_count.toLocaleString()} rows</span>
                          )}
                        </div>
                        <div className="columns-list">
                          <h6>Columns ({table.columns.length})</h6>
                          <div className="columns-grid">
                            {table.columns.map((column) => {
                              const columnKey = `${table.schema}.${table.name}.${column.name}`;
                              return (
                                <div key={columnKey} className="column-item">
                                  <span className="column-name">{column.name}</span>
                                  <span className="column-type">{column.type}</span>
                                  {column.primary_key && <span className="pk-badge">PK</span>}
                                  {!column.nullable && <span className="not-null-badge">NOT NULL</span>}
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {schemaInfo.views.length > 0 && (
                <div className="views-section">
                  <h4>👁️ Views ({schemaInfo.views.length})</h4>
                  <div className="views-list">
                    {schemaInfo.views.map((view) => {
                      const viewKey = `${view.schema}.${view.name}`;
                      return (
                        <div key={viewKey} className="view-card">
                          <h5>{view.name}</h5>
                          <span className="view-schema">{view.schema}</span>
                          <p>{view.columns.length} columns</p>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {showAddForm && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <h3>➕ Add Database Connection</h3>
              <button 
                className="close-btn"
                onClick={() => setShowAddForm(false)}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleAddConnection}>
              <div className="form-grid">
                <div className="form-group">
                  <label htmlFor="connection-name">Connection Name *</label>
                  <input
                    id="connection-name"
                    type="text"
                    value={formData.name}
                    onChange={(e) => setFormData({...formData, name: e.target.value})}
                    required
                    placeholder="My Database"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="database-type">Database Type *</label>
                  <select
                    id="database-type"
                    value={formData.type}
                    onChange={(e) => handleTypeChange(e.target.value)}
                    required
                  >
                    {supportedDbs.map((db) => (
                      <option key={db.type} value={db.type}>
                        {db.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="database-host">Host</label>
                  <input
                    id="database-host"
                    type="text"
                    value={formData.host}
                    onChange={(e) => setFormData({...formData, host: e.target.value})}
                    placeholder="localhost"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="database-port">Port</label>
                  <input
                    id="database-port"
                    type="number"
                    value={formData.port}
                    onChange={(e) => setFormData({...formData, port: Number.parseInt(e.target.value, 10)})}
                    placeholder="5432"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="database-name">Database Name</label>
                  <input
                    id="database-name"
                    type="text"
                    value={formData.database}
                    onChange={(e) => setFormData({...formData, database: e.target.value})}
                    placeholder="mydb"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="database-username">Username</label>
                  <input
                    id="database-username"
                    type="text"
                    value={formData.username}
                    onChange={(e) => setFormData({...formData, username: e.target.value})}
                    placeholder="user"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="database-password">Password</label>
                  <input
                    id="database-password"
                    type="password"
                    value={formData.password}
                    onChange={(e) => setFormData({...formData, password: e.target.value})}
                    placeholder="password"
                  />
                </div>

                <div className="form-group checkbox-group">
                  <label>
                    <input
                      type="checkbox"
                      checked={formData.ssl_enabled}
                      onChange={(e) => setFormData({...formData, ssl_enabled: e.target.checked})}
                    />
                    <span>Enable SSL</span>
                  </label>
                </div>
              </div>

              <div className="form-actions">
                <button 
                  type="button" 
                  onClick={() => setShowAddForm(false)}
                  className="cancel-btn"
                >
                  Cancel
                </button>
                <button 
                  type="submit" 
                  disabled={isLoading}
                  className="submit-btn"
                >
                  {isLoading ? 'Connecting...' : 'Add Connection'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default DatabaseConnector;