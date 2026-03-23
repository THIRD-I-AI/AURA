import React, { useState, useRef, useEffect } from 'react';
import '../styles/design-system.css';
import '../styles/components.css';
import Button from './ui/Button';
import Card from './ui/Card';
import RechartsVisualization from './RechartsVisualization';
import { chatService, executionService, type QueryResponse, type ExecutionResult } from '../services/api';

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
    userQuery?: string; // Store original user question for chart detection
  };
}

interface ChatInterfaceProps {
  sessionId?: string;
}

/**
 * Professional Chat Interface
 * Conversational UI with real backend integration for SQL generation and execution
 */
export const ChatInterface: React.FC<ChatInterfaceProps> = ({
  sessionId = `session_${Date.now()}`,
}) => {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      type: 'system',
      content: import.meta.env.VITE_WELCOME_MESSAGE || 'Welcome to AURA Analytics. I\'m here to help you analyze your data. Ask me anything about your datasets!',
      timestamp: new Date(),
    },
  ]);
  const [input, setInput] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Pick up "Re-run in Chat" from QueryHistory page
  useEffect(() => {
    const rerun = localStorage.getItem('rerunQuery');
    if (rerun) {
      localStorage.removeItem('rerunQuery');
      setInput(rerun);
    }
    // Pick up "Analyze with AI" from FilesAndData page
    const dataset = localStorage.getItem('active_dataset');
    if (dataset) {
      try {
        const ds = JSON.parse(dataset);
        localStorage.removeItem('active_dataset');
        setInput(`Analyze the ${ds.name} dataset — show me summary statistics and key insights`);
      } catch { /* ignore */ }
    }
  }, []);

  // Save a query execution to localStorage so QueryHistory page can display it
  const saveToQueryHistory = (prompt: string, sql: string, status: 'success' | 'error', rows: number, executionTime: number) => {
    try {
      const existing = JSON.parse(localStorage.getItem('queryHistory') || '[]');
      const record = {
        id: `q_${Date.now()}`,
        prompt,
        sql,
        status,
        rows,
        executionTime,
        timestamp: new Date().toISOString(),
      };
      existing.unshift(record);
      // Keep last 50 queries
      localStorage.setItem('queryHistory', JSON.stringify(existing.slice(0, 50)));
    } catch { /* ignore storage errors */ }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isProcessing) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: input,
      timestamp: new Date(),
    };

    const userInput = input;
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setIsProcessing(true);

    try {
      // Show loading indicator
      const loadingMessage: Message = {
        id: 'loading-' + Date.now(),
        type: 'assistant',
        content: 'Analyzing your request and generating SQL...',
        timestamp: new Date(),
        loading: true,
      };
      setMessages((prev) => [...prev, loadingMessage]);

      // Call unified /chat endpoint (NL → SQL → Execute → Visualize)
      const response: QueryResponse = await chatService.sendMessage(userInput, { sessionId });

      // Remove loading message
      setMessages((prev) => prev.filter((m) => m.id !== loadingMessage.id));

      if (response.status === 'Conversational') {
        const chatMessage: Message = {
          id: Date.now().toString(),
          type: 'assistant',
          content: response.message || 'I am not sure how to respond to that.',
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, chatMessage]);
      } else if (response.status === 'Success' || response.status === 'Fallback') {
        // Show generated SQL
        const sqlMessage: Message = {
          id: Date.now().toString(),
          type: 'sql',
          content: response.final_query || '-- No query generated',
          timestamp: new Date(),
          metadata: {
            query: response.final_query,
            jobId: response.job_id,
            userQuery: userInput,
          },
        };
        setMessages((prev) => [...prev, sqlMessage]);

        // If auto-execute returned results, show them immediately
        const execResult = response.execution_result;
        if (execResult && execResult.success && execResult.data?.length > 0) {
          const resultMessage: Message = {
            id: (Date.now() + 1).toString(),
            type: 'assistant',
            content: `✓ Query executed — ${execResult.row_count || execResult.data.length} rows in ${response.execution_time_ms || 0}ms`,
            timestamp: new Date(),
            metadata: {
              executionResult: {
                success: true,
                data: execResult.data,
                columns: execResult.columns,
                rows: execResult.rows,
                row_count: execResult.row_count,
                execution_time_ms: response.execution_time_ms,
                chart_spec: execResult.chart_spec,
                conclusion: execResult.conclusion,
              },
              userQuery: userInput,
            },
          };
          setMessages((prev) => [...prev, resultMessage]);
          saveToQueryHistory(userInput, response.final_query || '', 'success', execResult.row_count || execResult.data.length, response.execution_time_ms || 0);
        } else if (execResult && !execResult.success) {
          // Execution failed — show SQL with manual run option
          const assistantMessage: Message = {
            id: (Date.now() + 1).toString(),
            type: 'assistant',
            content: `SQL generated but execution failed: ${execResult.error || 'Unknown error'}. Click "Run Query" to retry.`,
            timestamp: new Date(),
          };
          setMessages((prev) => [...prev, assistantMessage]);
        } else {
          // No auto-execute or no data — prompt manual execution
          const assistantMessage: Message = {
            id: (Date.now() + 1).toString(),
            type: 'assistant',
            content:
              response.status === 'Fallback'
                ? 'Generated a fallback query. Click "Run Query" to execute.'
                : 'SQL query generated. Click "Run Query" to execute.',
            timestamp: new Date(),
          };
          setMessages((prev) => [...prev, assistantMessage]);
        }
      } else {
        // Show error
        const errorMessage: Message = {
          id: Date.now().toString(),
          type: 'assistant',
          content: `Error: ${response.error_message || 'Failed to generate query'}`,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errorMessage]);
      }
    } catch (error) {
      // Remove loading message if still present
      setMessages((prev) => prev.filter((m) => !m.loading));

      const errorMessage: Message = {
        id: 'error-' + Date.now(),
        type: 'assistant',
        content: error instanceof Error && error.message.includes('offline')
          ? 'Backend services are offline. Please ensure all services are running.'
          : `Error: ${error instanceof Error ? error.message : 'Failed to process request'}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleExecuteQuery = async (query: string, jobId?: string, userQuery?: string) => {
    setIsProcessing(true);

    try {
      // Show execution loading message
      const loadingMessage: Message = {
        id: 'exec-loading-' + Date.now(),
        type: 'assistant',
        content: jobId ? `Executing query (Job: ${jobId})...` : 'Executing query...',
        timestamp: new Date(),
        loading: true,
      };
      setMessages((prev) => [...prev, loadingMessage]);

      // Execute the SQL
      const result: ExecutionResult = await executionService.executeSql(query);

      // Remove loading message
      setMessages((prev) => prev.filter((m) => m.id !== loadingMessage.id));

      if (result.success && result.data) {
        // Show results
        const resultMessage: Message = {
          id: Date.now().toString(),
          type: 'assistant',
          content: `✓ Query executed successfully. Retrieved ${result.row_count || result.data.length} rows in ${result.execution_time_ms || 0}ms.`,
          timestamp: new Date(),
          metadata: {
            executionResult: result,
            userQuery: userQuery, // Pass user query for chart detection
          },
        };
        setMessages((prev) => [...prev, resultMessage]);
        saveToQueryHistory(userQuery || query, query, 'success', result.row_count || result.data.length, result.execution_time_ms || 0);
      } else {
        // Show error
        const errorMessage: Message = {
          id: Date.now().toString(),
          type: 'assistant',
          content: `✗ Execution failed: ${result.error || 'Unknown error'}`,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errorMessage]);
        saveToQueryHistory(userQuery || query, query, 'error', 0, 0);
      }
    } catch (error) {
      setMessages((prev) => prev.filter((m) => !m.loading));

      const errorMessage: Message = {
        id: 'error-' + Date.now(),
        type: 'assistant',
        content: `Execution error: ${error instanceof Error ? error.message : 'Unknown error'}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsProcessing(false);
    }
  };

  const quickSuggestions = [
    '📊 Show me sales trends',
    '📈 Revenue analysis',
    '🔍 Top products',
    '📋 Generate summary',
  ];

  return (
    <Card className="chat-container">
      {/* Chat History */}
      <div
        style={{
          height: '500px',
          overflow: 'auto',
          padding: 'var(--space-6)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-4)',
          backgroundColor: 'var(--bg-primary)',
        }}
      >
        {messages.length === 1 && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 'var(--space-4)',
              alignItems: 'center',
              justifyContent: 'center',
              textAlign: 'center',
              flex: 1,
              opacity: 0.7,
            }}
          >
            <div style={{ fontSize: '3rem' }}>💬</div>
            <h3 style={{ margin: 0, fontSize: 'var(--font-lg)' }}>Start a conversation</h3>
            <p style={{ margin: 'var(--space-2) 0 0 0', color: 'var(--text-secondary)' }}>
              Ask questions about your data and I'll help you analyze it.
            </p>

            {/* Quick Suggestions */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(2, 1fr)',
                gap: 'var(--space-3)',
                marginTop: 'var(--space-4)',
                width: '100%',
                maxWidth: '400px',
              }}
            >
              {quickSuggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => setInput(suggestion.substring(2))}
                  style={{
                    padding: 'var(--space-3)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: 'var(--bg-secondary)',
                    cursor: 'pointer',
                    fontSize: 'var(--font-xs)',
                    color: 'var(--text-secondary)',
                    transition: 'all var(--transition-fast)',
                    textAlign: 'center',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = 'var(--color-primary-500)';
                    e.currentTarget.style.backgroundColor = 'var(--color-primary-50)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = 'var(--border-default)';
                    e.currentTarget.style.backgroundColor = 'var(--bg-secondary)';
                  }}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Messages */}
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} onExecuteQuery={handleExecuteQuery} />
        ))}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div
        style={{
          borderTop: '1px solid var(--border-default)',
          padding: 'var(--space-4)',
          backgroundColor: 'var(--bg-secondary)',
        }}
      >
        <form onSubmit={handleSendMessage} style={{ display: 'flex', gap: 'var(--space-3)' }}>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your data..."
            disabled={isProcessing}
            style={{
              flex: 1,
              padding: 'var(--size-input-padding-y) var(--size-input-padding-x)',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-md)',
              fontSize: 'var(--font-sm)',
              fontFamily: 'var(--font-sans)',
              backgroundColor: 'var(--bg-primary)',
              color: 'var(--text-primary)',
              transition: 'all var(--transition-fast)',
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = 'var(--color-primary-500)';
              e.currentTarget.style.boxShadow = '0 0 0 3px var(--color-primary-50)';
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = 'var(--border-default)';
              e.currentTarget.style.boxShadow = 'none';
            }}
          />
          <Button
            type="submit"
            variant="primary"
            size="md"
            disabled={!input.trim() || isProcessing}
            isLoading={isProcessing}
            rightIcon="→"
          >
            Send
          </Button>
        </form>
      </div>
    </Card>
  );
};

interface MessageBubbleProps {
  message: Message;
  onExecuteQuery?: (query: string, jobId?: string, userQuery?: string) => void;
}

const MessageBubble: React.FC<MessageBubbleProps> = ({ message, onExecuteQuery }) => {
  const isUser = message.type === 'user';
  const isSQL = message.type === 'sql';
  const hasResults = message.metadata?.executionResult;

  if (isSQL && message.metadata?.query) {
    // SQL Code Block with Execute Button
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        <div
          style={{
            padding: 'var(--space-4)',
            backgroundColor: 'var(--bg-tertiary)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-default)',
            fontFamily: 'monospace',
            fontSize: 'var(--font-sm)',
            color: 'var(--text-primary)',
            whiteSpace: 'pre-wrap',
            overflowX: 'auto',
          }}
        >
          <div style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-xs)', marginBottom: 'var(--space-2)' }}>
            GENERATED SQL
          </div>
          {message.content}
        </div>
        <div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => onExecuteQuery?.(message.metadata!.query!, message.metadata!.jobId, message.metadata!.userQuery)}
          >
            ▶ Run Query
          </Button>
        </div>
      </div>
    );
  }

  if (hasResults) {
    const result = message.metadata!.executionResult!;
    const userQuery = message.metadata!.userQuery || ''; // Get the original user query
    
    // Determine if we should show a chart based on the query content
    const shouldShowChart = result.data && result.data.length > 0 && result.data.length <= 100;
    
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        <div style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-sm)' }}>
          {message.content}
        </div>
        
        {/* Analytical Conclusion / Insight */}
        {result.conclusion && (
          <div
            style={{
              padding: 'var(--space-3)',
              backgroundColor: 'var(--color-primary-50)',
              border: '1px solid var(--color-primary-200)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--color-primary-900)',
              fontSize: 'var(--font-sm)',
              marginBottom: 'var(--space-2)',
            }}
          >
            <strong>💡 Insight:</strong> {result.conclusion}
          </div>
        )}
        
        {/* Auto-visualization - show chart before table */}
        {shouldShowChart && (
          <RechartsVisualization
            data={result.data!}
            type="auto"
            userQuery={userQuery}
            height={350}
          />
        )}
        
        {/* Data table */}
        {result.data && result.data.length > 0 && (
          <div
            style={{
              maxHeight: '300px',
              overflow: 'auto',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-md)',
            }}
          >
            <table
              style={{
                width: '100%',
                borderCollapse: 'collapse',
                fontSize: 'var(--font-xs)',
              }}
            >
              <thead>
                <tr style={{ backgroundColor: 'var(--bg-tertiary)', position: 'sticky', top: 0 }}>
                  {result.columns?.map((col) => (
                    <th
                      key={col}
                      style={{
                        padding: 'var(--space-2) var(--space-3)',
                        textAlign: 'left',
                        fontWeight: 'var(--weight-semibold)',
                        borderBottom: '2px solid var(--border-default)',
                      }}
                    >
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.data.slice(0, 100).map((row, idx) => (
                  <tr
                    key={idx}
                    style={{
                      backgroundColor: idx % 2 === 0 ? 'transparent' : 'var(--bg-secondary)',
                    }}
                  >
                    {result.columns?.map((col) => (
                      <td
                        key={col}
                        style={{
                          padding: 'var(--space-2) var(--space-3)',
                          borderBottom: '1px solid var(--border-subtle)',
                        }}
                      >
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

  return (
    <div
      style={{
        display: 'flex',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        gap: 'var(--space-3)',
      }}
    >
      {!isUser && (
        <div
          style={{
            width: '2rem',
            height: '2rem',
            borderRadius: 'var(--radius-full)',
            backgroundColor: 'var(--bg-secondary)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 'var(--font-lg)',
            flexShrink: 0,
          }}
        >
          {message.type === 'system' ? '🤖' : '✨'}
        </div>
      )}

      <div
        style={{
          maxWidth: '70%',
          backgroundColor: isUser ? 'var(--color-primary-500)' : 'var(--bg-secondary)',
          color: isUser ? 'white' : 'var(--text-primary)',
          padding: 'var(--space-3) var(--space-4)',
          borderRadius: isUser ? 'var(--radius-xl) var(--radius-md) var(--radius-md) var(--radius-xl)' : 'var(--radius-md) var(--radius-xl) var(--radius-xl) var(--radius-md)',
          fontSize: 'var(--font-sm)',
          lineHeight: 'var(--line-normal)',
          animation: message.loading ? 'pulse 2s infinite' : 'fadeIn 0.3s ease-out',
        }}
      >
        {message.loading ? (
          <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
            <span className="spinner" style={{ borderColor: isUser ? 'rgba(255,255,255,0.3)' : undefined, borderTopColor: isUser ? 'white' : undefined }} />
            <span>{message.content}</span>
          </div>
        ) : (
          message.content
        )}
      </div>

      {isUser && (
        <div
          style={{
            width: '2rem',
            height: '2rem',
            borderRadius: 'var(--radius-full)',
            backgroundColor: 'var(--color-primary-100)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 'var(--font-lg)',
            flexShrink: 0,
          }}
        >
          👤
        </div>
      )}
    </div>
  );
};

const styles = `
  @keyframes fadeIn {
    from {
      opacity: 0;
      transform: translateY(0.5rem);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  @keyframes pulse {
    0%, 100% {
      opacity: 1;
    }
    50% {
      opacity: 0.5;
    }
  }
`;

if (typeof document !== 'undefined') {
  const styleSheet = document.createElement('style');
  styleSheet.textContent = styles;
  document.head.appendChild(styleSheet);
}

export default ChatInterface;
