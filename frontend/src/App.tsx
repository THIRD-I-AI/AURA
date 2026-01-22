import { useState, useEffect, useCallback } from 'react';
import './App.css';
import { ThemeProvider } from './contexts/ThemeContext';
import NavigationBar from './components/NavigationBar';
import ChatArea from './components/ChatArea';
import ResultsArea from './components/ResultsArea';
import TrendAnalysis from './components/TrendAnalysis';
import ResizableLayout from './components/Layout/ResizableLayout';
import LeftSidebar from './components/LeftSidebar';
import GlassBox from './components/GlassBox';
import BackgroundParticles from './components/BackgroundParticles';
import DatabaseConnector from './components/DatabaseConnector';
import PipelinesPanel from './components/Pipelines/PipelinesPanel';
import ErrorBoundary from './components/ErrorBoundary';
import type { DataResult } from './types';

import type { Message } from './components/MessageList';

type SidebarConnection = {
  id: string;
  name: string;
  type: string;
  status: 'connected' | 'disconnected' | 'connecting';
  host?: string;
  database?: string;
  lastConnected?: Date;
};

type BackendConnection = {
  id: string;
  name: string;
  type: string;
  host: string;
  port: number | null;
  database: string;
  is_active: boolean;
  updated_at?: string | null;
};

function AppContent() {
  const [currentMode, setCurrentMode] = useState<'chat' | 'database' | 'visualization' | 'strategic' | 'pipelines'>('chat');
  const [sqlQuery, setSqlQuery] = useState('-- Generated SQL will appear here');
  const [dataResult, setDataResult] = useState<DataResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [pendingApproval, setPendingApproval] = useState(false);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [currentPrompt, setCurrentPrompt] = useState<string>('');

  
  // New dynamic features
  const [messages, setMessages] = useState<Message[]>([]);
  const [isTyping, setIsTyping] = useState(false);
  const [uploadedData, setUploadedData] = useState<any[] | null>(null);
  const [uploadedFileName, setUploadedFileName] = useState<string>('');
  const [uploadedColumns, setUploadedColumns] = useState<string[]>([]);
  const [showTrendAnalysis, setShowTrendAnalysis] = useState(false);

  const [connections, setConnections] = useState<SidebarConnection[]>([]);

  // Dynamic chat system
  const addMessage = (type: 'user' | 'assistant' | 'system', content: string, data?: any) => {
    const newMessage: Message = {
      id: Date.now().toString(),
      type,
      content,
      timestamp: new Date(),
      data
    };
    setMessages(prev => [...prev, newMessage]);
    return newMessage.id;
  };

  const handleQuickAction = (action: string) => {
    let prompt = '';
    switch (action) {
      case 'analyze_trends':
        prompt = 'Show me sales trends for the last quarter';
        break;
      case 'create_chart':
        prompt = 'Create a visualization of my data';
        break;
      case 'connect_database':
        setCurrentMode('database');
        return;
      case 'analyze_data':
        if (uploadedData) {
          setShowTrendAnalysis(true);
        } else {
          prompt = 'Help me analyze my data patterns';
        }
        break;
      default:
        prompt = action;
    }
    if (prompt) {
      handleQuerySubmit(prompt);
    }
  };

  const handleFileUpload = (file: File, data: any[]) => {
    const columns = data.length > 0 ? Object.keys(data[0]) : [];
    setUploadedData(data);
    setUploadedFileName(file.name);
    setUploadedColumns(columns);
    setShowTrendAnalysis(true);
    
    // Add system message about file upload
    addMessage('system', `Successfully uploaded ${file.name} with ${data.length} records`, {
      fileName: file.name,
      recordCount: data.length,
      columns: columns
    });
  };

  const handleConnectionSelect = (connection: any) => {
    addMessage('system', `🔌 Connected to: ${connection.name}`);
    console.log('Selected connection:', connection);
  };

  const mapConnection = useCallback((connection: BackendConnection): SidebarConnection => {
    const hostDisplay = connection.host
      ? [connection.host, connection.port ?? undefined].filter(Boolean).join(':')
      : undefined;

    const updatedAt = connection.updated_at ? new Date(connection.updated_at) : undefined;
    const lastConnected = updatedAt && !Number.isNaN(updatedAt.getTime()) ? updatedAt : undefined;

    return {
      id: connection.id,
      name: connection.name,
      type: connection.type,
      status: connection.is_active ? 'connected' : 'disconnected',
      host: hostDisplay,
      database: connection.database,
      lastConnected
    };
  }, []);

  const refreshConnections = useCallback(async () => {
    try {
      const response = await fetch('http://localhost:8002/connections');
      if (!response.ok) {
        throw new Error(`Failed to load connections (${response.status})`);
      }
      const data: BackendConnection[] = await response.json();
      setConnections(data.map(mapConnection));
    } catch (error) {
      console.error('Error refreshing database connections', error);
    }
  }, [mapConnection]);

  useEffect(() => {
    refreshConnections();
  }, [refreshConnections]);

  const handleConnectionsUpdated = useCallback(
    (updatedConnections: BackendConnection[]) => {
      setConnections(updatedConnections.map(mapConnection));
    },
    [mapConnection]
  );

  const handleNewConnection = () => {
    addMessage('system', '➕ Opening new connection dialog...');
    setCurrentMode('database');
    refreshConnections();
  };

  const handleQuerySubmit = async (prompt: string) => {
    // Add user message
    addMessage('user', prompt);
    
    setIsTyping(true);
    setIsLoading(true);
    setPendingApproval(false);
    setDataResult(null);
    setCurrentPrompt(prompt);
    
    try {
      // Build context from uploaded data if available
      let context = 'Schema: sales_table(product_name, total_revenue, sale_date)';
      if (uploadedFileName && uploadedColumns.length > 0) {
        const tableName = uploadedFileName.replace(/\.[^/.]+$/, '').replace(/[^a-zA-Z0-9_]/g, '_');
        const columnsSchema = uploadedColumns.join(', ');
        context = `File: ${uploadedFileName}\nSchema: ${tableName}(${columnsSchema})\nRows: ${uploadedData?.length || 0}`;
      }
      
      const response = await fetch('http://localhost:8000/generate_query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          session_id: 'frontend-session',
          prompt: prompt,
          context: context,
          uploaded_file: uploadedFileName || null,
          columns: uploadedColumns.length > 0 ? uploadedColumns : null
        })
      });
      
      const result = await response.json();
      
      const isSuccess = result.status === 'Success' || result.status === 'Fallback';
      if (isSuccess) {
        const finalQuery = result.final_query || '-- No SQL generated';
        setSqlQuery(finalQuery);
        setCurrentJobId(result.job_id || 'job_' + Date.now());
        
        // Add assistant response (note fallback is still a usable query)
        const responderText = result.status === 'Fallback'
          ? 'Generated a fallback query from your file schema. Please review it below for approval.'
          : 'I\'ve analyzed your request and generated a query. Please review it below for approval.';
        addMessage('assistant', responderText, {
          query: finalQuery,
          jobId: result.job_id
        });
        
        setPendingApproval(true);
      } else {
        const errMsg = result.error_message || 'Unknown error';
        setSqlQuery('-- Error: ' + errMsg);
        addMessage('assistant', 'I encountered an error while processing your request: ' + errMsg);
        setPendingApproval(false);
      }
    } catch (error) {
      console.error('Error calling API:', error);
      setSqlQuery('-- Error: Failed to connect to backend');
      addMessage('assistant', 'Sorry, I\'m having trouble connecting to the analysis service. Please try again.');
      setPendingApproval(false);
    } finally {
      setIsLoading(false);
      setIsTyping(false);
    }
  };

  const handleSqlApproval = async (approved: boolean, editedQuery?: string) => {
    if (!currentJobId) return;
    
    setPendingApproval(false);
    
    if (approved) {
      setIsLoading(true);
      try {
        // If user edited the query, update it
        if (editedQuery && editedQuery !== sqlQuery) {
          setSqlQuery(editedQuery);
        }
        
        // Simulate execution with comprehensive mock data
        await new Promise(resolve => setTimeout(resolve, 1000));
        
        // Generate sample data based on the query type
        const sampleData = generateSampleData(currentPrompt);
        setDataResult(sampleData);
      } catch (error) {
        console.error('Execution error:', error);
      } finally {
        setIsLoading(false);
      }
    } else {
      setSqlQuery('-- Query execution cancelled by user');
      setDataResult(null);
    }
    
    setCurrentJobId(null);
  };



  const handleModeChange = (mode: 'chat' | 'database' | 'visualization' | 'strategic' | 'pipelines') => {
    setCurrentMode(mode);
    if (mode === 'database') {
      refreshConnections();
    }
  };

  const appClassName = currentMode === 'database' ? 'app database-mode' : 'app';

  return (
    <div className={appClassName}>
      <BackgroundParticles />
      <NavigationBar currentMode={currentMode} onModeChange={handleModeChange} />
      {currentMode === 'database' ? (
        <div className="database-mode-wrapper">
          <DatabaseConnector onConnectionsUpdated={handleConnectionsUpdated} />
        </div>
      ) : currentMode === 'pipelines' ? (
        <div className="pipelines-mode-wrapper">
          <ErrorBoundary>
            <PipelinesPanel />
          </ErrorBoundary>
        </div>
      ) : (
        <ResizableLayout
          leftPanel={{
            id: 'sidebar',
            title: 'Data Sources',
            content: (
              <LeftSidebar
                connections={connections}
                onConnectionSelect={handleConnectionSelect}
                onNewConnection={handleNewConnection}
                onFileUpload={handleFileUpload}
                isLoading={isLoading}
              />
            ),
            minWidth: 250,
            defaultWidth: 300
          }}
          middleTopPanel={{
            id: 'results',
            title: 'Query Results',
            content: (
              <ResultsArea 
                sqlQuery={sqlQuery} 
                dataResult={dataResult} 
                pendingApproval={pendingApproval}
                onApproval={handleSqlApproval}
                isLoading={isLoading}
              />
            ),
            minHeight: 200,
            defaultHeight: 400
          }}
          middleBottomPanel={{
            id: 'glassbox',
            title: 'Analytics Dashboard',
            content: (
              <GlassBox title="Dynamic Glass Panel">
                {showTrendAnalysis && uploadedData && (
                  <TrendAnalysis 
                    data={uploadedData}
                    fileName={uploadedFileName}
                  />
                )}
              </GlassBox>
            )
          }}
          rightPanel={{
            id: 'chat',
            title: 'AI Assistant',
            content: (
              <ChatArea 
                onQuerySubmit={handleQuerySubmit} 
                isLoading={isLoading}
                messages={messages}
                isTyping={isTyping}
                onQuickAction={handleQuickAction}
              />
            ),
            minWidth: 300,
            defaultWidth: 400
          }}
        />
      )}
    </div>
  );
}

// Enhanced sample data generation for comprehensive chart demonstrations
function generateSampleData(prompt: string): DataResult {
  const promptLower = prompt.toLowerCase();
  
  if (promptLower.includes('top') && promptLower.includes('product')) {
    return {
      columns: ['product_name', 'total_revenue', 'units_sold', 'profit_margin'],
      rows: [
        ['Premium Widget Pro', 125000, 2500, 35.2],
        ['Advanced Gadget Plus', 98000, 1960, 31.8],
        ['Smart Tool Elite', 87500, 1750, 28.9],
        ['Basic Widget Standard', 65000, 2600, 24.5],
        ['Compact Gadget Mini', 54000, 1800, 29.7],
        ['Universal Tool Kit', 43000, 860, 38.1],
        ['Economy Widget Lite', 32000, 1600, 18.3],
        ['Portable Gadget Go', 28000, 1400, 22.6],
        ['Essential Tool Set', 21000, 700, 26.4],
        ['Starter Widget Basic', 15000, 1000, 15.8],
        ['Pro Max Edition', 89000, 1200, 42.1],
        ['Deluxe Package', 76000, 950, 33.7]
      ]
    };
  } else if (promptLower.includes('revenue') && (promptLower.includes('month') || promptLower.includes('trend'))) {
    return {
      columns: ['month', 'total_revenue', 'order_count', 'avg_order_value'],
      rows: [
        ['2024-01', 180000, 1200, 150],
        ['2024-02', 195000, 1350, 144],
        ['2024-03', 165000, 1100, 150],
        ['2024-04', 220000, 1450, 152],
        ['2024-05', 235000, 1520, 155],
        ['2024-06', 210000, 1380, 152],
        ['2024-07', 245000, 1600, 153],
        ['2024-08', 230000, 1480, 155],
        ['2024-09', 255000, 1650, 155],
        ['2024-10', 275000, 1750, 157],
        ['2024-11', 290000, 1820, 159],
        ['2024-12', 310000, 1900, 163]
      ]
    };
  } else if (promptLower.includes('distribution') || promptLower.includes('histogram')) {
    return {
      columns: ['value_range', 'frequency', 'percentage'],
      rows: [
  ['0-10K', 45, 15],
        ['10K-20K', 67, 22.3],
        ['20K-30K', 89, 29.7],
        ['30K-40K', 56, 18.7],
        ['40K-50K', 34, 11.3],
        ['50K-60K', 23, 7.7],
  ['60K-70K', 12, 4],
        ['70K-80K', 8, 2.7],
        ['80K-90K', 5, 1.7],
  ['90K-100K', 3, 1]
      ]
    };
  } else if (promptLower.includes('region') || promptLower.includes('location')) {
    return {
      columns: ['region', 'sales', 'customers', 'avg_revenue'],
      rows: [
        ['North America', 450000, 2500, 180],
        ['Europe', 380000, 2100, 181],
        ['Asia Pacific', 520000, 2800, 186],
        ['Latin America', 240000, 1200, 200],
        ['Middle East', 180000, 950, 189],
        ['Africa', 120000, 600, 200]
      ]
    };
  } else if (promptLower.includes('category') || promptLower.includes('segment')) {
    return {
      columns: ['category', 'revenue', 'market_share', 'growth_rate'],
      rows: [
        ['Electronics', 1250000, 35.8, 12.5],
        ['Software', 980000, 28.1, 18.3],
        ['Hardware', 650000, 18.6, 8.7],
  ['Services', 420000, 12, 22.1],
        ['Accessories', 190000, 5.5, 15.2]
      ]
    };
  } else if (promptLower.includes('performance') || promptLower.includes('kpi')) {
    return {
      columns: ['metric', 'q1', 'q2', 'q3', 'q4'],
      rows: [
        ['Revenue', 180000, 220000, 245000, 290000],
        ['Customers', 1200, 1450, 1600, 1850],
        ['Orders', 2400, 2900, 3200, 3700],
        ['Conversion', 3.2, 3.8, 4.1, 4.5]
      ]
    };
  } else {
    return {
      columns: ['product_name', 'total_revenue', 'sale_date', 'category', 'rating'],
      rows: [
        ['Widget A', 15000, '2024-01-15', 'Electronics', 4.5],
        ['Gadget B', 25000, '2024-01-20', 'Electronics', 4.8],
        ['Tool C', 8500, '2024-01-25', 'Hardware', 4.2],
        ['Widget D', 18000, '2024-02-01', 'Electronics', 4.6],
        ['Gadget E', 32000, '2024-02-05', 'Electronics', 4.9],
        ['Tool F', 12000, '2024-02-10', 'Hardware', 4.1],
        ['Premium Kit', 45000, '2024-02-15', 'Premium', 4.7],
        ['Smart Device', 38000, '2024-02-20', 'Electronics', 4.8]
      ]
    };
  }
}

export default function App() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}