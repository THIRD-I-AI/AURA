import React, { useState, useRef, useEffect } from 'react';
import '../styles/design-system.css';
import '../styles/components.css';
import Button from './ui/Button';
import RechartsVisualization from './RechartsVisualization';
import {
  chatService,
  executionService,
  analyticsService,
  type QueryResponse,
  type ExecutionResult,
} from '../services/api';

interface Message {
  id: string;
  type: 'user' | 'assistant' | 'system' | 'sql';
  content: string;
  timestamp: Date;
  loading?: boolean;
  metadata?: {
    query?: string;
    jobId?: string;
    executionResult?: ExecutionResult;
    userQuery?: string;
  };
}

interface ChatInterfaceProps {
  sessionId?: string;
}

/* ── SVG icons ────────────────────────────────────────────────────── */
const AuraIcon = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
    <circle cx="7" cy="7" r="6" stroke="currentColor" strokeWidth="1.3"/>
    <path d="M4.5 9L7 4l2.5 5M5.5 7.5h3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

const SendIcon = () => (
  <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
    <path d="M1.5 7.5L13.5 1.5L9 13.5L7.5 8.5L1.5 7.5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>
    <path d="M7.5 8.5L13.5 1.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
  </svg>
);

const RunIcon = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
    <path d="M3 2.5L9.5 6L3 9.5V2.5Z" fill="currentColor"/>
  </svg>
);

const CopyIcon = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
    <rect x="4" y="4" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="1.2"/>
    <path d="M8 4V2a1 1 0 00-1-1H2a1 1 0 00-1 1v5a1 1 0 001 1h2" stroke="currentColor" strokeWidth="1.2"/>
  </svg>
);

const SUGGESTIONS = [
  'Show sales trends over time',
  'Top 10 products by revenue',
  'Monthly active users',
  'Data quality summary',
];

/* ── Main component ───────────────────────────────────────────────── */
export const ChatInterface: React.FC<ChatInterfaceProps> = ({
  sessionId = `session_${Date.now()}`,
}) => {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      type: 'system',
      content:
        import.meta.env.VITE_WELCOME_MESSAGE ||
        'Ask me anything about your data — I generate SQL, run it, and visualize the results.',
      timestamp: new Date(),
    },
  ]);
  const [input, setInput] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    const rerun = localStorage.getItem('rerunQuery');
    if (rerun) { localStorage.removeItem('rerunQuery'); setInput(rerun); }
    const dataset = localStorage.getItem('active_dataset');
    if (dataset) {
      try {
        const ds = JSON.parse(dataset);
        localStorage.removeItem('active_dataset');
        setInput(`Analyze the ${ds.name} dataset — show me summary statistics and key insights`);
      } catch { /* ignore */ }
    }
  }, []);

  const saveToQueryHistory = (prompt: string, sql: string, status: 'success' | 'error', rows: number, executionTime: number) => {
    const record = { id: `q_${Date.now()}`, prompt, sql, status, rows, executionTime, timestamp: new Date().toISOString() };
    analyticsService.saveQueryRecord(record).catch(() => {});
    try {
      const existing = JSON.parse(localStorage.getItem('queryHistory') || '[]');
      existing.unshift(record);
      localStorage.setItem('queryHistory', JSON.stringify(existing.slice(0, 50)));
    } catch { /* ignore */ }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isProcessing) return;

    const userInput = input;
    setMessages((prev) => [...prev, { id: Date.now().toString(), type: 'user', content: userInput, timestamp: new Date() }]);
    setInput('');
    setIsProcessing(true);

    const loadingId = 'loading-' + Date.now();
    setMessages((prev) => [...prev, { id: loadingId, type: 'assistant', content: 'Analyzing and generating SQL…', timestamp: new Date(), loading: true }]);

    try {
      const response: QueryResponse = await chatService.sendMessage(userInput, { sessionId });
      setMessages((prev) => prev.filter((m) => m.id !== loadingId));

      if (response.status === 'Conversational') {
        setMessages((prev) => [...prev, { id: Date.now().toString(), type: 'assistant', content: response.message || 'How can I help?', timestamp: new Date() }]);
      } else if (response.status === 'Success' || response.status === 'Fallback') {
        setMessages((prev) => [...prev, {
          id: Date.now().toString(), type: 'sql', content: response.final_query || '-- No query generated',
          timestamp: new Date(), metadata: { query: response.final_query, jobId: response.job_id, userQuery: userInput },
        }]);
        const execResult = response.execution_result;
        if (execResult?.success && (execResult.data?.length ?? 0) > 0) {
          setMessages((prev) => [...prev, {
            id: (Date.now() + 1).toString(), type: 'assistant',
            content: `Returned ${execResult.row_count || execResult.data!.length} rows in ${response.execution_time_ms || 0}ms`,
            timestamp: new Date(),
            metadata: { executionResult: { success: true, data: execResult.data, columns: execResult.columns, rows: execResult.rows, row_count: execResult.row_count, execution_time_ms: response.execution_time_ms, chart_spec: execResult.chart_spec, conclusion: execResult.conclusion }, userQuery: userInput },
          }]);
          saveToQueryHistory(userInput, response.final_query || '', 'success', execResult.row_count || execResult.data!.length, response.execution_time_ms || 0);
        } else {
          setMessages((prev) => [...prev, { id: (Date.now() + 1).toString(), type: 'assistant', content: execResult && !execResult.success ? `Execution failed: ${execResult.error}. Click Run to retry.` : 'SQL ready — click Run to execute.', timestamp: new Date() }]);
        }
      } else {
        setMessages((prev) => [...prev, { id: Date.now().toString(), type: 'assistant', content: `Error: ${response.error_message || 'Failed to generate query'}`, timestamp: new Date() }]);
      }
    } catch (error) {
      setMessages((prev) => prev.filter((m) => !m.loading));
      setMessages((prev) => [...prev, { id: 'error-' + Date.now(), type: 'assistant', content: `Error: ${error instanceof Error ? error.message : 'Failed to process request'}`, timestamp: new Date() }]);
    } finally {
      setIsProcessing(false);
      inputRef.current?.focus();
    }
  };

  const handleExecuteQuery = async (query: string, jobId?: string, userQuery?: string) => {
    setIsProcessing(true);
    const loadingId = 'exec-loading-' + Date.now();
    setMessages((prev) => [...prev, { id: loadingId, type: 'assistant', content: 'Executing…', timestamp: new Date(), loading: true }]);
    try {
      const result: ExecutionResult = await executionService.executeSql(query);
      setMessages((prev) => prev.filter((m) => m.id !== loadingId));
      if (result.success && result.data) {
        setMessages((prev) => [...prev, { id: Date.now().toString(), type: 'assistant', content: `Returned ${result.row_count || result.data!.length} rows in ${result.execution_time_ms || 0}ms`, timestamp: new Date(), metadata: { executionResult: result, userQuery } }]);
        saveToQueryHistory(userQuery || query, query, 'success', result.row_count || result.data.length, result.execution_time_ms || 0);
      } else {
        setMessages((prev) => [...prev, { id: Date.now().toString(), type: 'assistant', content: `Execution failed: ${result.error || 'Unknown error'}`, timestamp: new Date() }]);
        saveToQueryHistory(userQuery || query, query, 'error', 0, 0);
      }
    } catch (error) {
      setMessages((prev) => prev.filter((m) => !m.loading));
      setMessages((prev) => [...prev, { id: 'error-' + Date.now(), type: 'assistant', content: `Execution error: ${error instanceof Error ? error.message : 'Unknown error'}`, timestamp: new Date() }]);
    } finally {
      setIsProcessing(false);
    }
  };

  const isEmpty = messages.length === 1 && messages[0].type === 'system';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'var(--bg-surface)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>

      {/* ── Header ──────────────────────────────────────────── */}
      <div style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', gap: 'var(--space-2-5)', flexShrink: 0 }}>
        <div style={{ width: 24, height: 24, borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent) 0%, #7c3aed 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', flexShrink: 0 }}>
          <AuraIcon />
        </div>
        <span style={{ fontSize: 'var(--font-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>AURA Assistant</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 4 }}>
          <span className="status-dot status-dot--live" style={{ width: 6, height: 6 }} />
          Ready
        </span>
      </div>

      {/* ── Messages ────────────────────────────────────────── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>

        {isEmpty && (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 'var(--space-5)', padding: 'var(--space-8) var(--space-4)', textAlign: 'center' }}>
            <div style={{ width: 48, height: 48, borderRadius: '50%', background: 'var(--accent-dim)', border: '1px solid var(--accent-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent)' }}>
              <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
                <path d="M4 11C4 7.13 7.13 4 11 4s7 3.13 7 7-3.13 7-7 7H3L4 16" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                <path d="M8 11h6M8 8h3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
            </div>
            <div>
              <h3 style={{ margin: '0 0 6px', fontSize: 'var(--font-lg)', fontWeight: 600, color: 'var(--text-primary)' }}>Ask about your data</h3>
              <p style={{ margin: 0, fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)', lineHeight: 1.6, maxWidth: 280 }}>
                I generate SQL, execute it, and visualize results in one step.
              </p>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-2)', width: '100%', maxWidth: 360 }}>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => { setInput(s); inputRef.current?.focus(); }}
                  style={{ padding: 'var(--space-2-5) var(--space-3)', background: 'var(--bg-surface-2)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', color: 'var(--text-secondary)', fontSize: 'var(--font-xs)', cursor: 'pointer', textAlign: 'left', transition: 'all var(--dur-fast)', fontFamily: 'var(--font-sans)', lineHeight: 1.4 }}
                  onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent-border)'; (e.currentTarget as HTMLButtonElement).style.color = '#93c5fd'; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border-default)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)'; }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} onExecuteQuery={handleExecuteQuery} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* ── Input ───────────────────────────────────────────── */}
      <div style={{ padding: 'var(--space-3) var(--space-4)', borderTop: '1px solid var(--border-subtle)', flexShrink: 0 }}>
        <form onSubmit={handleSendMessage} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your data…"
            disabled={isProcessing}
            style={{
              flex: 1,
              height: 36,
              padding: '0 var(--space-3)',
              background: 'var(--bg-sunken)',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-md)',
              fontSize: 'var(--font-sm)',
              fontFamily: 'var(--font-sans)',
              color: 'var(--text-primary)',
              outline: 'none',
              transition: 'border-color var(--dur-fast), box-shadow var(--dur-fast)',
            }}
            onFocus={e => { e.currentTarget.style.borderColor = 'var(--border-focus)'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(59,130,246,0.12)'; }}
            onBlur={e => { e.currentTarget.style.borderColor = 'var(--border-default)'; e.currentTarget.style.boxShadow = 'none'; }}
          />
          <Button type="submit" variant="primary" size="sm" disabled={!input.trim() || isProcessing} isLoading={isProcessing} rightIcon={<SendIcon />}>
            Send
          </Button>
        </form>
        <p style={{ margin: '6px 0 0', fontSize: 11, color: 'var(--text-disabled)', textAlign: 'center' }}>
          NL → SQL → Execute → Visualize
        </p>
      </div>
    </div>
  );
};

/* ── Message Bubble ───────────────────────────────────────────────── */
const MessageBubble: React.FC<{
  message: Message;
  onExecuteQuery?: (query: string, jobId?: string, userQuery?: string) => void;
}> = ({ message, onExecuteQuery }) => {
  const [copied, setCopied] = useState(false);

  const copySQL = (code: string) => {
    navigator.clipboard?.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  };

  // ── SQL block ────────────────────────────────────────────────
  if (message.type === 'sql' && message.metadata?.query) {
    return (
      <div style={{ animation: 'fade-in 0.2s ease-out' }}>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
          <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
            <rect x="1" y="1" width="9" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.2"/>
            <path d="M3 4l2 2-2 2M6.5 8h2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          GENERATED SQL
        </div>
        <div style={{ position: 'relative', background: 'var(--bg-sunken)', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
          <pre style={{ margin: 0, padding: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 12, color: '#a5b4fc', lineHeight: 1.7, overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {message.content}
          </pre>
          <button
            onClick={() => copySQL(message.content)}
            style={{ position: 'absolute', top: 8, right: 8, display: 'flex', alignItems: 'center', gap: 4, padding: '3px 8px', background: 'var(--bg-elevated)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)', color: 'var(--text-tertiary)', fontSize: 11, cursor: 'pointer', fontFamily: 'var(--font-sans)', transition: 'all var(--dur-fast)' }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-primary)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-tertiary)')}
          >
            <CopyIcon />
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        <div style={{ marginTop: 8 }}>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<RunIcon />}
            onClick={() => onExecuteQuery?.(message.metadata!.query!, message.metadata!.jobId, message.metadata!.userQuery)}
          >
            Run Query
          </Button>
        </div>
      </div>
    );
  }

  // ── Results block ─────────────────────────────────────────────
  if (message.metadata?.executionResult) {
    const result = message.metadata.executionResult;
    const showChart = result.data && result.data.length > 0 && result.data.length <= 100;

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)', animation: 'fade-in 0.2s ease-out' }}>
        {/* Status line */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--font-xs)', color: 'var(--green)' }}>
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <circle cx="6.5" cy="6.5" r="5.5" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M4 6.5l2 2 3-3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          {message.content}
        </div>

        {/* Insight */}
        {result.conclusion && (
          <div style={{ padding: 'var(--space-3)', background: 'var(--accent-dim)', border: '1px solid var(--accent-border)', borderRadius: 'var(--radius-md)', fontSize: 'var(--font-xs)', color: '#93c5fd', lineHeight: 1.6 }}>
            <span style={{ fontWeight: 600, marginRight: 6 }}>Insight</span>{result.conclusion}
          </div>
        )}

        {/* Chart */}
        {showChart && (
          <div style={{ borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
            <RechartsVisualization data={result.data!} type="auto" userQuery={message.metadata?.userQuery} height={300} />
          </div>
        )}

        {/* Table */}
        {result.data && result.data.length > 0 && (
          <div style={{ maxHeight: 280, overflow: 'auto', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: 'var(--bg-surface-2)', position: 'sticky', top: 0, zIndex: 1 }}>
                  {result.columns?.map((col) => (
                    <th key={col} style={{ padding: '7px 12px', textAlign: 'left', fontWeight: 600, fontSize: 11, color: 'var(--text-tertiary)', letterSpacing: '0.06em', textTransform: 'uppercase', borderBottom: '1px solid var(--border-default)', whiteSpace: 'nowrap' }}>
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.data.slice(0, 100).map((row, idx) => (
                  <tr key={idx} style={{ borderBottom: '1px solid var(--border-hairline)', background: idx % 2 === 1 ? 'var(--bg-hover)' : 'transparent' }}>
                    {result.columns?.map((col) => (
                      <td key={col} style={{ padding: '6px 12px', color: 'var(--text-secondary)', fontFamily: typeof row[col] === 'number' ? 'var(--font-mono)' : undefined, fontSize: 12 }}>
                        {String(row[col] ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  // ── Regular text message ─────────────────────────────────────
  const isUser = message.type === 'user';

  return (
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', gap: 8, alignItems: 'flex-end', animation: 'fade-in 0.2s ease-out' }}>
      {!isUser && (
        <div style={{ width: 24, height: 24, borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent) 0%, #7c3aed 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', flexShrink: 0, marginBottom: 2 }}>
          <AuraIcon />
        </div>
      )}

      <div style={{
        maxWidth: '72%',
        padding: '8px 12px',
        borderRadius: isUser ? '12px 12px 3px 12px' : '3px 12px 12px 12px',
        background: isUser ? 'var(--accent)' : 'var(--bg-surface-2)',
        border: isUser ? 'none' : '1px solid var(--border-subtle)',
        color: isUser ? '#fff' : 'var(--text-primary)',
        fontSize: 'var(--font-sm)',
        lineHeight: 1.55,
      }}>
        {message.loading ? (
          <span style={{ display: 'flex', gap: 5, alignItems: 'center', color: 'var(--text-tertiary)' }}>
            <span className="spinner" style={{ width: 12, height: 12, borderWidth: 1.5 }} />
            {message.content}
          </span>
        ) : message.content}
      </div>

      {isUser && (
        <div style={{ width: 24, height: 24, borderRadius: '50%', background: 'var(--bg-elevated)', border: '1px solid var(--border-default)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginBottom: 2 }}>
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <circle cx="6.5" cy="5" r="2.5" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M2 11.5c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
        </div>
      )}
    </div>
  );
};

export default ChatInterface;
