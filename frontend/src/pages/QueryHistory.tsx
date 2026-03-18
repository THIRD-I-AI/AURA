import React, { useState, useEffect } from 'react';
import { type PageType } from '../components/Layout/AppLayout';
import './QueryHistory.css';

interface QueryHistoryProps {
  setCurrentPage?: (page: PageType) => void;
}

interface QueryRecord {
  id: string;
  prompt: string;
  sql: string;
  status: 'success' | 'error' | 'pending';
  rows: number;
  executionTime: number;
  timestamp: string;
}

const QueryHistory: React.FC<QueryHistoryProps> = ({ setCurrentPage }) => {
  const [queries, setQueries] = useState<QueryRecord[]>([]);
  const [selectedQuery, setSelectedQuery] = useState<QueryRecord | null>(null);
  const [filter, setFilter] = useState<'all' | 'success' | 'error'>('all');

  useEffect(() => {
    const saved = localStorage.getItem('queryHistory');
    if (saved) {
      try {
        setQueries(JSON.parse(saved));
      } catch (e) {
        console.error('Failed to load query history:', e);
      }
    }
  }, []);

  const filtered = filter === 'all' ? queries : queries.filter((q) => q.status === filter);

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'success': return '✅';
      case 'error': return '❌';
      case 'pending': return '⏳';
      default: return '❔';
    }
  };

  const formatTime = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  const formatDate = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleString();
    } catch {
      return iso;
    }
  };

  const handleRerun = (query: QueryRecord) => {
    localStorage.setItem('rerunQuery', query.prompt);
    if (setCurrentPage) {
      setCurrentPage('chat');
    }
  };

  return (
    <div className="query-history-page">
      <div className="query-history-container">

        {/* Header */}
        <header className="query-history-header">
          <div className="query-history-header-left">
            <h1 className="query-history-title">Query History</h1>
            <p className="query-history-subtitle">
              {queries.length} queries executed · Last 30 days
            </p>
          </div>
          <div className="query-history-filters">
            {(['all', 'success', 'error'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`query-history-filter-btn ${filter === f ? 'active' : ''}`}
              >
                {f === 'all' ? 'All' : f === 'success' ? '✅ Success' : '❌ Errors'}
              </button>
            ))}
          </div>
        </header>

        <div className="query-history-layout">

          {/* Query List */}
          <div className="query-history-list-panel">
            {filtered.length > 0 ? (
              filtered.map((q) => (
                <button
                  key={q.id}
                  onClick={() => setSelectedQuery(q)}
                  className={`query-history-item ${selectedQuery?.id === q.id ? 'selected' : ''}`}
                >
                  <div className="query-history-item-top">
                    <span className="query-history-item-icon">{getStatusIcon(q.status)}</span>
                    <span className="query-history-item-prompt">{q.prompt}</span>
                  </div>
                  <div className="query-history-item-meta">
                    <span>{formatDate(q.timestamp)}</span>
                    <span>{q.rows} rows</span>
                    <span>{formatTime(q.executionTime)}</span>
                  </div>
                </button>
              ))
            ) : (
              <div className="query-history-empty">
                <div className="query-history-empty-icon">📋</div>
                <p className="query-history-empty-title">No queries yet</p>
                <p className="query-history-empty-text">
                  Queries you run from the Chat workspace will appear here.
                </p>
                <button
                  className="query-history-cta"
                  onClick={() => setCurrentPage?.('chat')}
                >
                  Open Chat →
                </button>
              </div>
            )}
          </div>

          {/* Detail Panel */}
          <div className="query-history-detail-panel">
            {selectedQuery ? (
              <div className="query-history-detail-card">
                <div className="query-history-detail-status">
                  <span className={`query-history-badge ${selectedQuery.status}`}>
                    {getStatusIcon(selectedQuery.status)} {selectedQuery.status}
                  </span>
                  <span className="query-history-detail-time">
                    {formatDate(selectedQuery.timestamp)}
                  </span>
                </div>

                <h2 className="query-history-detail-prompt">
                  {selectedQuery.prompt}
                </h2>

                <div className="query-history-sql-block">
                  <div className="query-history-sql-header">
                    <span>Generated SQL</span>
                    <button
                      className="query-history-copy-btn"
                      onClick={() => navigator.clipboard.writeText(selectedQuery.sql)}
                    >
                      📋 Copy
                    </button>
                  </div>
                  <pre className="query-history-sql-code">
                    <code>{selectedQuery.sql}</code>
                  </pre>
                </div>

                <div className="query-history-stats-row">
                  <div className="query-history-stat">
                    <span className="query-history-stat-label">Rows</span>
                    <span className="query-history-stat-value">{selectedQuery.rows.toLocaleString()}</span>
                  </div>
                  <div className="query-history-stat">
                    <span className="query-history-stat-label">Duration</span>
                    <span className="query-history-stat-value">{formatTime(selectedQuery.executionTime)}</span>
                  </div>
                  <div className="query-history-stat">
                    <span className="query-history-stat-label">Status</span>
                    <span className="query-history-stat-value">{selectedQuery.status}</span>
                  </div>
                </div>

                <button className="query-history-rerun-btn" onClick={() => handleRerun(selectedQuery)}>
                  🔄 Re-run in Chat
                </button>
              </div>
            ) : (
              <div className="query-history-empty-detail">
                <p className="query-history-empty-detail-title">Select a query</p>
                <p className="query-history-empty-detail-text">
                  Click a query from the list to view its details, SQL, and results.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default QueryHistory;
