import React, { useState, useEffect } from 'react';
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { type PageType } from '../components/Layout/AppLayout';
import { useAuraStore } from '../store';
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

// ── SVG icons ─────────────────────────────────────────────────────────────────

const CheckIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#34d399" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12"/>
  </svg>
);

const XIcon = ({ color = '#f87171' }: { color?: string }) => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round">
    <line x1="18" y1="6" x2="6" y2="18"/>
    <line x1="6" y1="6" x2="18" y2="18"/>
  </svg>
);

const ClockIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" strokeWidth="2" strokeLinecap="round">
    <circle cx="12" cy="12" r="10"/>
    <polyline points="12 6 12 12 16 14"/>
  </svg>
);

const ListIcon = () => (
  <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--text-disabled)' }}>
    <line x1="8" y1="6"  x2="21" y2="6"/>
    <line x1="8" y1="12" x2="21" y2="12"/>
    <line x1="8" y1="18" x2="21" y2="18"/>
    <line x1="3" y1="6"  x2="3.01" y2="6"/>
    <line x1="3" y1="12" x2="3.01" y2="12"/>
    <line x1="3" y1="18" x2="3.01" y2="18"/>
  </svg>
);

const RefreshIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 4 23 10 17 10"/>
    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
  </svg>
);

const CopyIcon = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
  </svg>
);

const PlayIcon = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="5 3 19 12 5 21 5 3"/>
  </svg>
);

// ─────────────────────────────────────────────────────────────────────────────

const kpiCardStyle: React.CSSProperties = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-default)',
  borderRadius: 'var(--radius-lg)',
  padding: 'var(--space-4) var(--space-5)',
  display: 'flex',
  flexDirection: 'column',
  gap: 'var(--space-1)',
  minWidth: 0,
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

const PIE_COLORS = ['#34d399', '#f87171', '#fbbf24'];

// ─────────────────────────────────────────────────────────────────────────────

const QueryHistory: React.FC<QueryHistoryProps> = ({ setCurrentPage }) => {
  const {
    state: { queryHistory, queryHistoryLoading },
    actions: { fetchQueryHistory },
  } = useAuraStore();

  const [selectedQuery, setSelectedQuery] = useState<QueryRecord | null>(null);
  const [filter, setFilter] = useState<'all' | 'success' | 'error'>('all');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetchQueryHistory(100, filter === 'all' ? undefined : filter);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const queries = queryHistory as QueryRecord[];
  const filtered =
    filter === 'all' ? queries : queries.filter((q) => q.status === filter);

  // ── KPI calculations ─────────────────────────────────────────────
  const successCount = queries.filter((q) => q.status === 'success').length;
  const errorCount = queries.filter((q) => q.status === 'error').length;
  const successRate =
    queries.length > 0 ? Math.round((successCount / queries.length) * 100) : 0;
  const totalRows = queries.reduce((sum, q) => sum + (q.rows || 0), 0);
  const avgTime =
    queries.length > 0
      ? queries.reduce((sum, q) => sum + (q.executionTime || 0), 0) / queries.length
      : 0;

  const pieData = [
    { name: 'Success', value: successCount },
    { name: 'Error', value: errorCount },
    { name: 'Pending', value: queries.filter((q) => q.status === 'pending').length },
  ].filter((d) => d.value > 0);

  // ── Helpers ──────────────────────────────────────────────────────
  const formatTime = (ms: number) => {
    if (!ms || ms <= 0) return '—';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  const handleCopy = (sql: string) => {
    navigator.clipboard.writeText(sql);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleRerun = (query: QueryRecord) => {
    localStorage.setItem('rerunQuery', query.prompt);
    setCurrentPage?.('chat');
  };

  const StatusIcon = ({ status }: { status: string }) => {
    if (status === 'success') return <CheckIcon />;
    if (status === 'error') return <XIcon />;
    if (status === 'pending') return <ClockIcon />;
    return null;
  };

  const statusBadgeCls = (status: string) => {
    if (status === 'success') return 'q-status q-status--success';
    if (status === 'error') return 'q-status q-status--error';
    return 'q-status q-status--running';
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', height: '100%', minHeight: 0 }}>

      {/* ── KPI stats bar ────────────────────────────────────────── */}
      <div
        style={{
          display: 'grid',
          // Wrap from a 5-up row down to 2-up / 1-up instead of crushing the
          // four KPI tiles + pie into slivers on narrow screens.
          gridTemplateColumns: 'repeat(auto-fit, minmax(min(160px, 100%), 1fr))',
          gap: 'var(--space-3)',
          alignItems: 'stretch',
          flexShrink: 0,
        }}
      >
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>Total Queries</span>
          <span style={kpiValueStyle}>{queries.length}</span>
        </div>
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>Success Rate</span>
          <span style={{ ...kpiValueStyle, color: successRate >= 80 ? '#34d399' : successRate >= 50 ? '#fbbf24' : '#f87171' }}>
            {queries.length > 0 ? `${successRate}%` : '—'}
          </span>
        </div>
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>Avg Exec Time</span>
          <span style={kpiValueStyle}>{queries.length > 0 ? formatTime(Math.round(avgTime)) : '—'}</span>
        </div>
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>Total Rows</span>
          <span style={kpiValueStyle}>{totalRows > 0 ? totalRows.toLocaleString() : '—'}</span>
        </div>

        {/* Mini pie chart */}
        {pieData.length > 0 && (
          <div
            style={{
              ...kpiCardStyle,
              flexDirection: 'row',
              alignItems: 'center',
              gap: 'var(--space-3)',
              padding: 'var(--space-2) var(--space-4)',
            }}
          >
            <ResponsiveContainer width={64} height={64}>
              <PieChart>
                <Pie data={pieData} cx="50%" cy="50%" innerRadius={18} outerRadius={30} paddingAngle={2} dataKey="value">
                  {pieData.map((_entry, index) => (
                    <Cell key={index} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-md)',
                    fontSize: 11,
                    color: 'var(--text-primary)',
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {pieData.map((d, i) => (
                <div key={d.name} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                  <span
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: '50%',
                      background: PIE_COLORS[i % PIE_COLORS.length],
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>{d.name}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Two-column layout ────────────────────────────────────── */}
      <div className="query-history-page" style={{ flex: 1, minHeight: 0 }}>

        {/* Left: query list */}
        <div className="query-list">
          <div className="query-list__header">
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <div>
                <span
                  style={{
                    fontSize: 'var(--font-base)',
                    fontWeight: 600,
                    color: 'var(--text-primary)',
                  }}
                >
                  Query History
                </span>
                <span
                  style={{
                    fontSize: 'var(--font-xs)',
                    color: 'var(--text-tertiary)',
                    marginLeft: 'var(--space-2)',
                  }}
                >
                  {queryHistoryLoading ? 'Loading…' : `${filtered.length} queries`}
                </span>
              </div>
              <button
                onClick={() => fetchQueryHistory(100, filter === 'all' ? undefined : filter)}
                title="Refresh"
                style={{
                  padding: 'var(--space-1-5)',
                  background: 'transparent',
                  border: '1px solid var(--border-default)',
                  borderRadius: 'var(--radius-md)',
                  color: 'var(--text-tertiary)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  transition: 'all var(--dur-fast)',
                }}
              >
                <RefreshIcon />
              </button>
            </div>
            <div className="query-filter-bar">
              {(['all', 'success', 'error'] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={`query-filter-btn${filter === f ? ' query-filter-btn--active' : ''}`}
                >
                  {f === 'all' ? 'All' : f === 'success' ? 'Success' : 'Errors'}
                </button>
              ))}
            </div>
          </div>

          <div className="query-list__items">
            {filtered.length === 0 ? (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  height: '100%',
                  gap: 'var(--space-3)',
                  padding: 'var(--space-8)',
                  textAlign: 'center',
                }}
              >
                <ListIcon />
                <p style={{ fontSize: 'var(--font-sm)', fontWeight: 600, color: 'var(--text-secondary)', margin: 0 }}>
                  No queries yet
                </p>
                <p style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', margin: 0 }}>
                  Queries you run from the Chat workspace will appear here.
                </p>
                <button
                  onClick={() => setCurrentPage?.('chat')}
                  style={{
                    padding: 'var(--space-2) var(--space-4)',
                    background: 'var(--accent-dim)',
                    border: '1px solid var(--accent-border)',
                    borderRadius: 'var(--radius-md)',
                    color: '#93c5fd',
                    fontSize: 'var(--font-xs)',
                    fontWeight: 600,
                    cursor: 'pointer',
                    fontFamily: 'var(--font-sans)',
                  }}
                >
                  Open Chat
                </button>
              </div>
            ) : (
              filtered.map((q) => (
                <button
                  key={q.id}
                  onClick={() => setSelectedQuery(selectedQuery?.id === q.id ? null : q)}
                  className={`query-list-item${selectedQuery?.id === q.id ? ' query-list-item--active' : ''}`}
                  style={{ width: '100%', textAlign: 'left', background: 'transparent', border: 'none', fontFamily: 'var(--font-sans)' }}
                >
                  <div className="query-list-item__prompt">{q.prompt}</div>
                  <div className="query-list-item__meta">
                    <span className={statusBadgeCls(q.status)}>{q.status}</span>
                    <span>{formatDate(q.timestamp)}</span>
                    {q.rows > 0 && <span>{q.rows.toLocaleString()} rows</span>}
                    {q.executionTime > 0 && <span>{formatTime(q.executionTime)}</span>}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        {/* Right: detail panel */}
        <div className="query-detail">
          {selectedQuery ? (
            <>
              <div className="query-detail__header">
                <p
                  style={{
                    fontSize: 'var(--font-sm)',
                    color: 'var(--text-primary)',
                    fontWeight: 500,
                    margin: 0,
                    flex: 1,
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                  }}
                >
                  {selectedQuery.prompt}
                </p>
                <span className={statusBadgeCls(selectedQuery.status)} style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <StatusIcon status={selectedQuery.status} />
                  {selectedQuery.status}
                </span>
              </div>

              <div className="query-detail__stats">
                <div className="query-stat">
                  <span className="query-stat__label">Rows Returned</span>
                  <span className="query-stat__value">{selectedQuery.rows?.toLocaleString() ?? '—'}</span>
                </div>
                <div className="query-stat">
                  <span className="query-stat__label">Execution Time</span>
                  <span className="query-stat__value">{formatTime(selectedQuery.executionTime)}</span>
                </div>
                <div className="query-stat">
                  <span className="query-stat__label">Status</span>
                  <span className="query-stat__value" style={{ color: selectedQuery.status === 'success' ? '#34d399' : selectedQuery.status === 'error' ? '#f87171' : '#fbbf24' }}>
                    {selectedQuery.status}
                  </span>
                </div>
              </div>

              <div className="query-detail__sql">
                <div
                  style={{
                    fontSize: '10px',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.07em',
                    color: 'var(--text-tertiary)',
                    marginBottom: 'var(--space-2)',
                  }}
                >
                  Generated SQL
                </div>
                <div className="sql-code-block">
                  <button
                    className="sql-copy-btn"
                    onClick={() => handleCopy(selectedQuery.sql)}
                  >
                    <CopyIcon />
                    {' '}
                    {copied ? 'Copied!' : 'Copy'}
                  </button>
                  {selectedQuery.sql || '— No SQL recorded —'}
                </div>

                <button
                  onClick={() => handleRerun(selectedQuery)}
                  style={{
                    marginTop: 'var(--space-4)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--space-2)',
                    padding: 'var(--space-2) var(--space-4)',
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-md)',
                    color: 'var(--text-secondary)',
                    fontSize: 'var(--font-xs)',
                    fontWeight: 600,
                    cursor: 'pointer',
                    fontFamily: 'var(--font-sans)',
                    transition: 'all var(--dur-fast)',
                  }}
                >
                  <PlayIcon /> Re-run in Chat
                </button>
              </div>
            </>
          ) : (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                gap: 'var(--space-3)',
                padding: 'var(--space-8)',
                textAlign: 'center',
              }}
            >
              <ListIcon />
              <p style={{ fontSize: 'var(--font-sm)', fontWeight: 600, color: 'var(--text-secondary)', margin: 0 }}>
                Select a query
              </p>
              <p style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', margin: 0 }}>
                Click a query from the list to view its SQL and execution stats.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default QueryHistory;
