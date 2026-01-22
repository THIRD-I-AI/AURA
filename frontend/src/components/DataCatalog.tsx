/**
 * DataCatalog Component
 * Browse and explore available data sources and tables
 */

import React, { useState, useEffect } from 'react';
import './DataCatalog.css';

interface CatalogTable {
  name: string;
  rows: number;
  columns: number;
  lastProfiled?: string;
  icon: string;
}

interface DataSource {
  id: string;
  name: string;
  type: string;
  icon: string;
  tables: CatalogTable[];
  connected: boolean;
}

const DataCatalog: React.FC = () => {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    // TODO: Fetch from connectorService.listSources()
    // For now, show empty state until backend integration
    setSources([]);
  }, []);

  const filteredTables = selectedSource
    ? sources
        .find((s) => s.id === selectedSource)
        ?.tables.filter((t) =>
          t.name.toLowerCase().includes(searchQuery.toLowerCase())
        ) || []
    : [];

  return (
    <div className="data-catalog">
      <div className="catalog-header">
        <h2>📊 Data Catalog</h2>
        <p>Explore available tables and metadata</p>
      </div>

      <div className="catalog-container">
        {/* Sources List */}
        <div className="sources-panel">
          <h3>Data Sources</h3>
          {sources.map((source) => (
            <div
              key={source.id}
              className={`source-item ${selectedSource === source.id ? 'active' : ''} ${
                source.connected ? 'connected' : 'disconnected'
              }`}
              onClick={() => setSelectedSource(source.id)}
            >
              <span className="source-icon">{source.icon}</span>
              <div className="source-info">
                <div className="source-name">{source.name}</div>
                <div className="source-type">{source.type}</div>
              </div>
              <div className={`status-badge ${source.connected ? 'connected' : ''}`}>
                {source.connected ? '✓' : '○'}
              </div>
            </div>
          ))}
        </div>

        {/* Tables List */}
        <div className="tables-panel">
          {selectedSource ? (
            <>
              <div className="tables-header">
                <h3>Tables in {sources.find((s) => s.id === selectedSource)?.name}</h3>
                <input
                  type="text"
                  placeholder="Search tables..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="search-input"
                />
              </div>

              <div className="tables-list">
                {filteredTables.length > 0 ? (
                  filteredTables.map((table) => (
                    <div key={table.name} className="table-item">
                      <div className="table-header">
                        <span className="table-icon">{table.icon}</span>
                        <span className="table-name">{table.name}</span>
                      </div>
                      <div className="table-stats">
                        <div className="stat">
                          <span className="stat-label">Rows:</span>
                          <span className="stat-value">{table.rows.toLocaleString()}</span>
                        </div>
                        <div className="stat">
                          <span className="stat-label">Columns:</span>
                          <span className="stat-value">{table.columns}</span>
                        </div>
                        {table.lastProfiled && (
                          <div className="stat">
                            <span className="stat-label">Last profiled:</span>
                            <span className="stat-value">{table.lastProfiled}</span>
                          </div>
                        )}
                      </div>
                      <div className="table-actions">
                        <button className="action-btn">Preview</button>
                        <button className="action-btn">Profile</button>
                        <button className="action-btn">Analyze</button>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="empty-state">No tables found</div>
                )}
              </div>
            </>
          ) : (
            <div className="empty-state">Select a data source to view tables</div>
          )}
        </div>
      </div>
    </div>
  );
};

export default DataCatalog;
