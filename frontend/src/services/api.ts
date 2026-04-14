/**
 * Production-Grade API Client for AURA Analytics
 * Centralized API communication layer with typed responses and error handling
 */

// =============================================================================
// Configuration
// =============================================================================

const API_BASE_URL = localStorage.getItem('apiUrl') || import.meta.env.VITE_API_URL || 'http://localhost:8000'; // Configurable via Settings page
const REQUEST_TIMEOUT = 300000; // 300 seconds (5 minutes) to allow massive agent queries
const HEALTH_CHECK_INTERVAL = Number(import.meta.env.VITE_HEALTH_CHECK_INTERVAL) || 10000; // 10 seconds for faster detection

// =============================================================================
// Type Definitions
// =============================================================================

export interface ApiError {
  message: string;
  status: number;
  code?: string;
  details?: unknown;
}

export interface HealthStatus {
  status: 'healthy' | 'degraded' | 'down';
  services?: {
    [key: string]: boolean;
  };
  timestamp: string;
}

export interface QueryResponse {
  job_id: string;
  status: 'Success' | 'Fallback' | 'Error' | 'Conversational';
  message?: string;
  final_query?: string;
  error_message?: string;
  execution_time_ms?: number;
  available_tables?: string[];
  execution_result?: ExecutionResult;
  metadata?: {
    execution_time_ms?: number;
    rows_affected?: number;
  };
}

export interface ExecutionResult {
  success: boolean;
  data?: Array<Record<string, any>>;
  columns?: string[];
  rows?: Array<Array<any>>;
  row_count?: number;
  execution_time_ms?: number;
  error?: string;
  chart_spec?: Record<string, any>;
  conclusion?: string;
}

export interface DataSource {
  id: string;
  name: string;
  type: 'postgresql' | 'mysql' | 'sqlite' | 'csv' | 'excel';
  host?: string;
  port?: number;
  database?: string;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface DashboardStats {
  total_rows: number;
  active_sources: number;
  file_sources?: number;
  queries_run: number;
  system_health: 'healthy' | 'degraded' | 'down';
  uptime_percentage?: number;
}

export interface ChatMessage {
  id: string;
  type: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  metadata?: {
    query?: string;
    job_id?: string;
  };
}

export interface ConnectionCredentials {
  name: string;
  type: 'postgresql' | 'mysql' | 'sqlite';
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  ssl?: boolean;
}

export interface UploadResponse {
  success: boolean;
  file_id: string;
  filename: string;
  rows: number;
  columns: string[];
  preview?: Array<Record<string, any>>;
}

// =============================================================================
// HTTP Client with Interceptors
// =============================================================================

class ApiClient {
  private baseURL: string;
  private timeout: number;
  private healthCheckTimer: number | null = null;
  private healthCallbacks: Set<(status: HealthStatus) => void> = new Set();

  constructor(baseURL: string, timeout: number) {
    this.baseURL = baseURL;
    this.timeout = timeout;
  }

  /**
   * Core fetch wrapper with timeout and error handling
   */
  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    try {
      const response = await fetch(url, {
        ...options,
        // Let browser set Content-Type automatically for FormData
        // Only set application/json for JSON payloads
        headers: {
          ...(options.body && typeof options.body === 'string' 
            ? { 'Content-Type': 'application/json' } 
            : {}),
          ...options.headers,
        },
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      // Handle HTTP errors
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw this.createError(
          errorData.message || response.statusText,
          response.status,
          errorData
        );
      }

      // Handle empty responses (204 No Content)
      if (response.status === 204) {
        return {} as T;
      }

      return await response.json();
    } catch (error) {
      clearTimeout(timeoutId);

      // Handle abort/timeout
      if (error instanceof Error && error.name === 'AbortError') {
        throw this.createError('Request timeout', 408);
      }

      // Handle network errors
      if (error instanceof TypeError) {
        throw this.createError(
          'Network error - backend may be offline',
          0,
          error
        );
      }

      // Re-throw ApiError instances
      if (this.isApiError(error)) {
        throw error;
      }

      // Unknown errors
      throw this.createError(
        error instanceof Error ? error.message : 'Unknown error',
        500,
        error
      );
    }
  }

  private createError(
    message: string,
    status: number,
    details?: unknown
  ): ApiError {
    return {
      message,
      status,
      code: status === 0 ? 'NETWORK_ERROR' : `HTTP_${status}`,
      details,
    };
  }

  private isApiError(error: unknown): error is ApiError {
    return (
      typeof error === 'object' &&
      error !== null &&
      'message' in error &&
      'status' in error
    );
  }

  // HTTP Methods
  async get<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, { method: 'GET' });
  }

  async post<T>(endpoint: string, body?: unknown): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async put<T>(endpoint: string, body?: unknown): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }

  async delete<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, { method: 'DELETE' });
  }

  /**
   * Upload file with FormData
   * Simplified upload with hardcoded backend URL and no headers for proper multipart/form-data handling
   */
  async uploadFile(file: File, uploadId?: string): Promise<any> {
    const TARGET_URL = `${API_BASE_URL}/upload`;
    console.log("🚀 STARTING UPLOAD TO:", TARGET_URL);

    const formData = new FormData();
    formData.append('file', file);

    const headers: Record<string, string> = {};
    if (uploadId) headers['X-Upload-Id'] = uploadId;

    try {
      const response = await fetch(TARGET_URL, {
        method: 'POST',
        body: formData,
        headers,
      });
      
      console.log("✅ RESPONSE STATUS:", response.status);
      
      if (!response.ok) {
        const text = await response.text();
        console.error("❌ UPLOAD FAILED:", text);
        throw new Error(`Upload failed: ${response.status} ${text}`);
      }
      
      return response.json();
    } catch (error) {
      console.error("🔥 NETWORK ERROR:", error);
      throw error;
    }
  }

  /**
   * Health check system
   */
  async checkHealth(): Promise<HealthStatus> {
    try {
      const response = await this.get<HealthStatus>('/health');
      return response;
    } catch {
      // Service unreachable — report as down
      return {
        status: 'down',
        timestamp: new Date().toISOString(),
      };
    }
  }

  startHealthMonitoring(callback: (status: HealthStatus) => void): void {
    this.healthCallbacks.add(callback);

    if (!this.healthCheckTimer) {
      // Initial check
      this.checkHealth().then((status) => {
        this.healthCallbacks.forEach((cb) => cb(status));
      });

      // Periodic checks
      this.healthCheckTimer = setInterval(async () => {
        const status = await this.checkHealth();
        this.healthCallbacks.forEach((cb) => cb(status));
      }, HEALTH_CHECK_INTERVAL);
    }
  }

  stopHealthMonitoring(callback?: (status: HealthStatus) => void): void {
    if (callback) {
      this.healthCallbacks.delete(callback);
    } else {
      this.healthCallbacks.clear();
    }

    if (this.healthCallbacks.size === 0 && this.healthCheckTimer) {
      clearInterval(this.healthCheckTimer);
      this.healthCheckTimer = null;
    }
  }
}

// =============================================================================
// Service Layer
// =============================================================================

const client = new ApiClient(API_BASE_URL, REQUEST_TIMEOUT);

/**
 * Chat Service - Natural language → SQL → Execute → Visualize
 * Uses the unified /chat endpoint for single-call pipeline
 */
export const chatService = {
  async sendMessage(
    message: string,
    context?: {
      sessionId?: string;
      uploadedFile?: string;
      columns?: string[];
    }
  ): Promise<QueryResponse> {
    return client.post<QueryResponse>('/chat', {
      message,
      session_id: context?.sessionId || `session_${Date.now()}`,
      context: context?.uploadedFile
        ? `File: ${context.uploadedFile}\nColumns: ${context.columns?.join(', ')}`
        : undefined,
      uploaded_file: context?.uploadedFile || null,
      columns: context?.columns || null,
      auto_execute: true,
    });
  },

  async getChatHistory(sessionId: string): Promise<ChatMessage[]> {
    try {
      return await client.get<ChatMessage[]>(`/chat/history/${sessionId}`);
    } catch {
      return [];
    }
  },

  async saveChatMessage(sessionId: string, message: { type: string; content: string; metadata?: any }): Promise<void> {
    try {
      await client.post(`/chat/history/${sessionId}`, message);
    } catch { /* best-effort */ }
  },
};

/**
 * Connector Service - Database connections management
 */
export const connectorService = {
  async listSources(): Promise<any> {
    return client.get<any>('/connections');
  },

  async registerSource(credentials: ConnectionCredentials): Promise<DataSource> {
    const resp = await client.post<{ success: boolean; connection: DataSource }>('/connections', credentials);
    return resp.connection;
  },

  async testConnection(connectionId: string): Promise<{ success: boolean; message: string }> {
    return client.post<{ success: boolean; message: string }>(
      `/connections/${connectionId}/test`,
      {}
    );
  },

  async deleteSource(connectionId: string): Promise<void> {
    return client.delete<void>(`/connections/${connectionId}`);
  },

  async getSupportedDatabases(): Promise<string[]> {
    try {
      const data = await client.get<{ connectors: Array<{ id: string }> }>('/connectors/available');
      return data.connectors.map(c => c.id);
    } catch {
      return ['postgresql', 'mysql', 'bigquery'];
    }
  },

  async getSchema(connectionId: string): Promise<Record<string, string[]>> {
    const resp = await client.get<{ success: boolean; schema: Record<string, string[]> }>(`/connections/${connectionId}/schema`);
    return resp.schema;
  },
};

/**
 * Analytics Service - Dashboard metrics and insights
 */
export const analyticsService = {
  async getDashboardStats(): Promise<DashboardStats> {
    try {
      return await client.get<DashboardStats>('/dashboard/stats');
    } catch {
      return {
        total_rows: 0,
        active_sources: 0,
        queries_run: 0,
        system_health: 'down',
      };
    }
  },

  async getInsights(datasetId: string): Promise<any> {
    return client.get<any>(`/insights/${datasetId}`);
  },

  async getQueryHistory(limit = 50, statusFilter?: string): Promise<{ success: boolean; queries: any[]; total: number }> {
    const params = statusFilter && statusFilter !== 'all' ? `?limit=${limit}&status_filter=${statusFilter}` : `?limit=${limit}`;
    return client.get(`/query-history${params}`);
  },

  async saveQueryRecord(record: { prompt: string; sql: string; status: string; rows: number; executionTime: number }): Promise<void> {
    try {
      await client.post('/query-history', record);
    } catch { /* best-effort */ }
  },
};

/**
 * Execution Service - SQL query execution
 */
export const executionService = {
  async executeSql(
    query: string,
    connectionId?: string
  ): Promise<ExecutionResult> {
    return client.post<ExecutionResult>('/execute', {
      sql: query,
      connection_id: connectionId || null,
    });
  },

  async approveExecution(jobId: string): Promise<ExecutionResult> {
    return client.post<ExecutionResult>(`/jobs/${jobId}/approve`, {});
  },

  async cancelExecution(jobId: string): Promise<void> {
    return client.post<void>(`/jobs/${jobId}/cancel`, {});
  },
};

/**
 * Upload Service - File uploads (CSV, Excel, Parquet)
 */
export const uploadService = {
  async uploadFile(file: File, uploadId?: string): Promise<UploadResponse> {
    return client.uploadFile(file, uploadId);
  },

  async getUploadedFiles(): Promise<Array<{ id: string; name: string; uploaded_at: string }>> {
    try {
      return await client.get<Array<{ id: string; name: string; uploaded_at: string }>>('/files');
    } catch {
      return [];
    }
  },
};

/**
 * Health Monitoring
 */
export const healthService = {
  checkHealth: () => client.checkHealth(),
  startMonitoring: (callback: (status: HealthStatus) => void) =>
    client.startHealthMonitoring(callback),
  stopMonitoring: (callback?: (status: HealthStatus) => void) =>
    client.stopHealthMonitoring(callback),
};

// =============================================================================
// ETL Pipeline Types & Service
// =============================================================================

export interface ETLColumnSchema {
  name: string;
  type: string;
}

export interface ETLTransformStep {
  id: string;
  type: 'filter' | 'rename' | 'drop_columns' | 'add_column' | 'sort' | 'aggregate' | 'deduplicate' | 'cast_type' | 'fill_missing' | 'custom_sql';
  description: string;
  config: Record<string, any>;
}

export interface ETLSourcePreview {
  status: string;
  source_file: string;
  table_name: string;
  columns: ETLColumnSchema[];
  row_count: number;
  preview: Array<Record<string, any>>;
  error?: string;
}

export interface ETLExecutionResult {
  status: string;
  pipeline_name: string;
  source: {
    file: string;
    row_count: number;
    columns: ETLColumnSchema[];
  };
  output: {
    row_count: number;
    columns: ETLColumnSchema[];
    file: string | null;
    format: string;
  };
  transform_sql: string;
  transforms_applied: number;
  preview: Array<Record<string, any>>;
  execution_time_ms: number;
  preview_only: boolean;
  error?: string;
}

export interface ETLNaturalLanguageResult {
  status: string;
  source_file: string;
  instruction: string;
  transforms: ETLTransformStep[];
  schema: ETLColumnSchema[];
  error?: string;
}

/**
 * ETL Pipeline Service
 */
export const etlService = {
  /** Preview source file schema and first N rows */
  async previewSource(sourceFile: string, limit = 20): Promise<ETLSourcePreview> {
    return client.post<ETLSourcePreview>('/etl/preview-source', {
      source_file: sourceFile,
      limit,
    });
  },

  /** Execute an ETL pipeline (or preview-only) */
  async execute(payload: {
    name: string;
    source_file: string;
    destination_format: string;
    destination_filename?: string;
    transforms: ETLTransformStep[];
    preview_only: boolean;
  }): Promise<ETLExecutionResult> {
    return client.post<ETLExecutionResult>('/etl/execute', payload);
  },

  /** Generate transforms from natural language instruction */
  async fromNaturalLanguage(
    sourceFile: string,
    instruction: string,
    destinationFormat = 'csv',
  ): Promise<ETLNaturalLanguageResult> {
    return client.post<ETLNaturalLanguageResult>('/etl/natural-language', {
      source_file: sourceFile,
      instruction,
      destination_format: destinationFormat,
    });
  },

  /** Get download URL for a processed file */
  getDownloadUrl(filename: string): string {
    return `${API_BASE_URL}/etl/download/${encodeURIComponent(filename)}`;
  },
};


// =============================================================================
// AI Pipeline Types & Service
// =============================================================================

export interface PipelineSourceDef {
  type: 'file' | 'postgresql' | 'mysql' | 'bigquery' | 'duckdb';
  file_name?: string;
  connection?: Record<string, any>;
  table?: string;
  query?: string;
}

export interface PipelineSinkDef {
  type: 'file' | 'postgresql' | 'duckdb' | 'preview';
  format?: string;
  file_name?: string;
  connection?: Record<string, any>;
  table?: string;
  if_exists?: string;
}

export interface PipelineStepDef {
  id?: string;
  type: string;
  description: string;
  config: Record<string, any>;
}

export interface PipelineDef {
  id?: string;
  name: string;
  description?: string;
  source: PipelineSourceDef;
  steps: PipelineStepDef[];
  sink: PipelineSinkDef;
  status?: string;
  created_at?: string;
  generated_from_prompt?: string;
  tags?: string[];
}

export interface PipelineRunResult {
  run_id: string;
  pipeline_id: string;
  status: string;
  rows_read: number;
  rows_written: number;
  columns_in: string[];
  columns_out: string[];
  output_file?: string;
  output_table?: string;
  sql_generated?: string;
  steps_executed: number;
  steps_skipped: number;
  preview_data?: Array<Record<string, any>>;
  error?: string;
  duration_ms: number;
}

export interface PipelineGenerateResponse {
  status: string;
  pipeline?: PipelineDef;
  error?: string;
}

export interface PipelineExecuteResponse {
  status: string;
  run?: PipelineRunResult;
  error?: string;
}

export interface PipelineListItem {
  id: string;
  name: string;
  description: string;
  source: string;
  steps: number;
  sink: string;
  status: string;
  created_at: string;
  tags: string[];
}

export const pipelineService = {
  /** Generate a pipeline from natural language */
  async generate(prompt: string, sourceFile?: string): Promise<PipelineGenerateResponse> {
    return client.post<PipelineGenerateResponse>('/pipeline/generate', {
      prompt,
      source_file: sourceFile || undefined,
      include_schema: true,
    });
  },

  /** Execute a pipeline definition */
  async execute(pipeline: PipelineDef, previewOnly = false): Promise<PipelineExecuteResponse> {
    return client.post<PipelineExecuteResponse>('/pipeline/execute', {
      pipeline,
      preview_only: previewOnly,
    });
  },

  /** Kick off execution in the background and get a run_id for SSE. */
  async executeAsync(
    pipeline: PipelineDef, previewOnly = false,
  ): Promise<{ status: string; run_id: string; topic: string }> {
    return client.post('/pipeline/execute/async', {
      pipeline,
      preview_only: previewOnly,
    });
  },

  /** Save a pipeline */
  async save(pipeline: PipelineDef): Promise<{ status: string; pipeline_id: string; name: string }> {
    return client.post('/pipeline/save', { pipeline });
  },

  /** List saved pipelines */
  async list(): Promise<{ status: string; count: number; pipelines: PipelineListItem[] }> {
    return client.get('/pipeline/list');
  },

  /** Get a pipeline by ID */
  async get(pipelineId: string): Promise<{ status: string; pipeline: PipelineDef }> {
    return client.get(`/pipeline/${encodeURIComponent(pipelineId)}`);
  },

  /** Delete a pipeline */
  async remove(pipelineId: string): Promise<{ status: string }> {
    return client.delete(`/pipeline/${encodeURIComponent(pipelineId)}`);
  },

  /** Get file schema for pipeline builder */
  async getFileSchema(fileName: string): Promise<{ status: string; schema: any }> {
    return client.get(`/pipeline/schema/${encodeURIComponent(fileName)}`);
  },

  /** Get download URL for pipeline output */
  getDownloadUrl(filename: string): string {
    return `${API_BASE_URL}/pipeline/download/${encodeURIComponent(filename)}`;
  },
};

// =============================================================================
// Streaming Pipeline Types & Service
// =============================================================================

export interface StreamPipelineSource {
  type: 'kafka' | 'file_watcher' | 'cdc' | 'websocket' | 'simulated';
  config: Record<string, any>;
}

export interface StreamPipelineSink {
  type: 'sse' | 'database' | 'file' | 'kafka' | 'alert' | 'console';
  config: Record<string, any>;
}

export interface StreamWindowConfig {
  type: 'tumbling' | 'sliding' | 'session' | 'global';
  size_seconds: number;
  slide_seconds?: number;
  gap_seconds?: number;
  late_data_policy: 'drop' | 'update' | 'dead_letter';
  allowed_lateness_seconds?: number;
}

export interface StreamTransform {
  id?: string;
  type: 'filter' | 'map' | 'aggregate' | 'flat_map' | 'key_by';
  description: string;
  config: Record<string, any>;
}

export interface StreamPipelineDef {
  id: string;
  name: string;
  description: string;
  source: StreamPipelineSource;
  event_time_field: string;
  watermark_delay_seconds: number;
  window: StreamWindowConfig;
  transforms: StreamTransform[];
  sinks: StreamPipelineSink[];
  checkpoint_interval_seconds: number;
  status: string;
  created_at: string;
  updated_at?: string;
  tags: string[];
  metrics?: StreamPipelineMetrics;
}

export interface StreamPipelineMetrics {
  pipeline_id: string;
  status: string;
  events_in: number;
  events_out: number;
  events_late: number;
  events_dropped: number;
  events_per_second: number;
  watermark_position: number;
  active_windows: number;
  closed_windows: number;
  last_checkpoint_at?: string;
  uptime_seconds: number;
  errors: string[];
}

export interface StreamTemplate {
  id: string;
  name: string;
  description: string;
  tags: string[];
  pipeline: Omit<StreamPipelineDef, 'id' | 'status' | 'created_at' | 'updated_at' | 'metrics'>;
}

/** Schema field descriptor — drives dynamic form rendering */
export interface SchemaField {
  key: string;
  label: string;
  type: 'text' | 'number' | 'select' | 'alert_rules' | 'agg_fields';
  default?: any;
  required?: boolean;
  help?: string;
  options?: string[];
}

export interface SourceSchema {
  label: string;
  description: string;
  implemented: boolean;
  fields: SchemaField[];
}

export interface SinkSchema {
  label: string;
  description: string;
  implemented: boolean;
  fields: SchemaField[];
  auto_add?: boolean;
}

export interface WindowSchema {
  label: string;
  description: string;
  fields: SchemaField[];
}

export interface TransformSchema {
  label: string;
  description: string;
  fields: SchemaField[];
}

export interface StreamingSchemas {
  sources: Record<string, SourceSchema>;
  sinks: Record<string, SinkSchema>;
  windows: Record<string, WindowSchema>;
  transforms: Record<string, TransformSchema>;
}

export const streamingService = {
  /** List all streaming pipelines */
  async list(): Promise<{ pipelines: StreamPipelineDef[]; total: number }> {
    return client.get('/streaming/pipelines');
  },

  /** Get a single pipeline with metrics */
  async get(pipelineId: string): Promise<StreamPipelineDef> {
    return client.get(`/streaming/pipelines/${encodeURIComponent(pipelineId)}`);
  },

  /** Create a new streaming pipeline */
  async create(pipeline: Omit<StreamPipelineDef, 'id' | 'status' | 'created_at' | 'updated_at' | 'metrics'> | Partial<StreamPipelineDef>): Promise<StreamPipelineDef> {
    return client.post('/streaming/pipelines', pipeline);
  },

  /** Update a pipeline */
  async update(pipelineId: string, updates: Partial<StreamPipelineDef>): Promise<StreamPipelineDef> {
    return client.put(`/streaming/pipelines/${encodeURIComponent(pipelineId)}`, updates);
  },

  /** Delete a pipeline */
  async remove(pipelineId: string): Promise<{ deleted: string }> {
    return client.delete(`/streaming/pipelines/${encodeURIComponent(pipelineId)}`);
  },

  /** Start a pipeline */
  async start(pipelineId: string): Promise<{ status: string; pipeline_id: string }> {
    return client.post(`/streaming/pipelines/${encodeURIComponent(pipelineId)}/start`, {});
  },

  /** Stop a pipeline */
  async stop(pipelineId: string): Promise<{ status: string; pipeline_id: string }> {
    return client.post(`/streaming/pipelines/${encodeURIComponent(pipelineId)}/stop`, {});
  },

  /** Pause a pipeline */
  async pause(pipelineId: string): Promise<{ status: string; pipeline_id: string }> {
    return client.post(`/streaming/pipelines/${encodeURIComponent(pipelineId)}/pause`, {});
  },

  /** Resume a paused pipeline */
  async resume(pipelineId: string): Promise<{ status: string; pipeline_id: string }> {
    return client.post(`/streaming/pipelines/${encodeURIComponent(pipelineId)}/resume`, {});
  },

  /** Get pipeline metrics */
  async metrics(pipelineId: string): Promise<StreamPipelineMetrics> {
    return client.get(`/streaming/pipelines/${encodeURIComponent(pipelineId)}/metrics`);
  },

  /** Get SSE event stream URL */
  streamUrl(pipelineId: string): string {
    return `${API_BASE_URL}/streaming/pipelines/${encodeURIComponent(pipelineId)}/stream`;
  },

  /** Get streaming templates */
  async templates(): Promise<{ templates: StreamTemplate[] }> {
    return client.get('/streaming/templates');
  },

  /** Get available source/sink/window/transform schemas for dynamic forms */
  async schemas(): Promise<StreamingSchemas> {
    return client.get('/streaming/schemas');
  },
};

// =============================================================================
// Exports
// =============================================================================

export { ApiClient };
export default client;
