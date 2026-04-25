/**
 * Production-Grade API Client for AURA Analytics
 * Centralized API communication layer with typed responses and error handling
 */

// =============================================================================
// Configuration
// =============================================================================

const _RAW_BASE = localStorage.getItem('apiUrl') || import.meta.env.VITE_API_URL || 'http://localhost:8000';
const ROOT_BASE_URL = _RAW_BASE.replace(/\/+$/, '');              // For non-versioned endpoints (e.g. /health)
const API_BASE_URL = `${ROOT_BASE_URL}/api/v1`;                   // All domain endpoints are versioned
const REQUEST_TIMEOUT = 300000; // 300 seconds (5 minutes) to allow massive agent queries
const HEALTH_CHECK_INTERVAL = Number(import.meta.env.VITE_HEALTH_CHECK_INTERVAL) || 10000; // 10 seconds for faster detection

// Workspace scoping is header-driven. The selected workspace ID persists
// across reloads via localStorage and is injected into every request.
export const DEFAULT_WORKSPACE_ID = 'default';
const WORKSPACE_STORAGE_KEY = 'aura.workspaceId';
let _currentWorkspaceId: string =
  (typeof localStorage !== 'undefined' && localStorage.getItem(WORKSPACE_STORAGE_KEY)) || DEFAULT_WORKSPACE_ID;
const _workspaceListeners = new Set<(id: string) => void>();

export function getCurrentWorkspaceId(): string {
  return _currentWorkspaceId;
}

export function setCurrentWorkspaceId(id: string): void {
  const next = (id || '').trim() || DEFAULT_WORKSPACE_ID;
  if (next === _currentWorkspaceId) return;
  _currentWorkspaceId = next;
  try { localStorage.setItem(WORKSPACE_STORAGE_KEY, next); } catch { /* storage full / blocked */ }
  _workspaceListeners.forEach((cb) => cb(next));
}

export function subscribeWorkspace(cb: (id: string) => void): () => void {
  _workspaceListeners.add(cb);
  return () => { _workspaceListeners.delete(cb); };
}

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
  sql_explanation?: string;
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
      // Inject X-Workspace-Id on every call except the public share endpoint
      // (which is intentionally unscoped — the token itself is the auth).
      const isPublic = endpoint.startsWith('/public/');
      const workspaceHeader = !isPublic ? { 'X-Workspace-Id': _currentWorkspaceId } : {};

      const response = await fetch(url, {
        ...options,
        // Let browser set Content-Type automatically for FormData
        // Only set application/json for JSON payloads
        headers: {
          ...(options.body && typeof options.body === 'string'
            ? { 'Content-Type': 'application/json' }
            : {}),
          ...workspaceHeader,
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

  async patch<T>(endpoint: string, body?: unknown): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  }

  async delete<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, { method: 'DELETE' });
  }

  /**
   * Upload file with FormData. Optional X-Upload-Id header lets the client
   * subscribe to SSE progress before posting.
   *
   * Routes through `request<T>` so the upload inherits the configured
   * timeout, ApiError normalization, and abort handling.
   */
  async uploadFile(file: File, uploadId?: string): Promise<any> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: Record<string, string> = {};
    if (uploadId) headers['X-Upload-Id'] = uploadId;

    return this.request('/upload', {
      method: 'POST',
      body: formData,
      headers,
    });
  }

  /**
   * Health check system
   */
  async checkHealth(): Promise<HealthStatus> {
    try {
      // Health endpoint lives at the root, not under /api/v1
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000);
      const response = await fetch(`${ROOT_BASE_URL}/health`, { signal: controller.signal });
      clearTimeout(timeoutId);
      if (!response.ok) throw new Error(response.statusText);
      return await response.json();
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
 * Connector registry types — mirror aurabackend/connectors/registry.py.
 * The registry is the single source of truth: new connectors register at
 * import time and the UI renders them generically off these specs.
 */
export type ConnectorFieldType = 'string' | 'secret' | 'number' | 'boolean' | 'textarea';
export type ConnectorKind = 'relational' | 'warehouse' | 'embedded' | 'stream';

export interface ConnectorField {
  key: string;
  label: string;
  type: ConnectorFieldType;
  required: boolean;
  default?: unknown;
  placeholder?: string | null;
  help?: string | null;
}

export interface ConnectorSpec {
  id: string;
  name: string;
  description: string;
  kind: ConnectorKind;
  icon: string;
  capabilities: string[];
  fields: ConnectorField[];
  available: boolean;
  unavailable_reason?: string | null;
  docs_url?: string | null;
  config_required?: string[];
}

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

  async registry(includeUnavailable = true): Promise<ConnectorSpec[]> {
    const data = await client.get<{ success: boolean; count: number; connectors: ConnectorSpec[] }>(
      `/connectors/registry?include_unavailable=${includeUnavailable}`,
    );
    return data.connectors;
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
 * Saved Queries (library) — list / create / rename + star / delete
 */
export interface SavedQuerySchedule {
  interval: 'hourly' | 'daily' | 'weekly';
  hour: number;
  minute: number;
  day_of_week?: number | null;
  enabled: boolean;
}

export interface SavedQuery {
  id: string;
  name: string;
  sql: string;
  prompt?: string | null;
  starred: boolean;
  created_at: string;
  updated_at: string;
  schedule?: SavedQuerySchedule | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
}

export interface SavedQueryRun {
  id: string;
  started_at: string;
  completed_at: string;
  status: 'success' | 'failed';
  row_count: number;
  execution_time_ms: number;
  error?: string | null;
}

export const savedQueryService = {
  async list(): Promise<SavedQuery[]> {
    const resp = await client.get<{ success: boolean; queries: SavedQuery[]; total: number }>('/saved-queries');
    return resp.queries ?? [];
  },
  async create(payload: { name: string; sql: string; prompt?: string; starred?: boolean }): Promise<SavedQuery> {
    const resp = await client.post<{ success: boolean; query: SavedQuery }>('/saved-queries', payload);
    return resp.query;
  },
  async update(id: string, patch: { name?: string; starred?: boolean }): Promise<SavedQuery> {
    const resp = await client.patch<{ success: boolean; query: SavedQuery }>(`/saved-queries/${encodeURIComponent(id)}`, patch);
    return resp.query;
  },
  async remove(id: string): Promise<void> {
    await client.delete<{ success: boolean; id: string }>(`/saved-queries/${encodeURIComponent(id)}`);
  },
  async setSchedule(id: string, schedule: SavedQuerySchedule): Promise<SavedQuery> {
    const resp = await client.put<{ success: boolean; query: SavedQuery }>(
      `/saved-queries/${encodeURIComponent(id)}/schedule`,
      schedule,
    );
    return resp.query;
  },
  async clearSchedule(id: string): Promise<SavedQuery> {
    const resp = await client.delete<{ success: boolean; query: SavedQuery }>(
      `/saved-queries/${encodeURIComponent(id)}/schedule`,
    );
    return resp.query;
  },
  async listRuns(id: string, limit = 20): Promise<SavedQueryRun[]> {
    const resp = await client.get<{ success: boolean; runs: SavedQueryRun[] }>(
      `/saved-queries/${encodeURIComponent(id)}/runs?limit=${limit}`,
    );
    return resp.runs ?? [];
  },
  async share(id: string): Promise<{ token: string; query_id: string }> {
    const resp = await client.post<{ success: boolean; token: string; query_id: string }>(
      `/saved-queries/${encodeURIComponent(id)}/share`,
    );
    return { token: resp.token, query_id: resp.query_id };
  },
  async unshare(id: string): Promise<number> {
    const resp = await client.delete<{ success: boolean; revoked: number }>(
      `/saved-queries/${encodeURIComponent(id)}/share`,
    );
    return resp.revoked ?? 0;
  },
  async getShared(token: string): Promise<SavedQuery> {
    const resp = await client.get<{ success: boolean; query: SavedQuery }>(
      `/public/saved-queries/${encodeURIComponent(token)}`,
    );
    return resp.query;
  },
  /** Builds the absolute URL that a recipient can open. */
  shareUrl(token: string): string {
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    return `${origin}/shared/query/${encodeURIComponent(token)}`;
  },
};

/**
 * Workspaces — lightweight tenancy layer. The selected workspace is
 * injected as ``X-Workspace-Id`` on every request via the ApiClient.
 */
export interface Workspace {
  id: string;
  name: string;
  description?: string | null;
  created_at: string;
  updated_at: string;
}

export const workspaceService = {
  async list(): Promise<Workspace[]> {
    const resp = await client.get<{ success: boolean; workspaces: Workspace[] }>('/workspaces');
    return resp.workspaces ?? [];
  },
  async create(payload: { name: string; description?: string }): Promise<Workspace> {
    const resp = await client.post<{ success: boolean; workspace: Workspace }>('/workspaces', payload);
    return resp.workspace;
  },
  async update(id: string, patch: { name?: string; description?: string }): Promise<Workspace> {
    const resp = await client.patch<{ success: boolean; workspace: Workspace }>(
      `/workspaces/${encodeURIComponent(id)}`,
      patch,
    );
    return resp.workspace;
  },
  async remove(id: string): Promise<void> {
    await client.delete<{ success: boolean; id: string }>(`/workspaces/${encodeURIComponent(id)}`);
  },
};

/**
 * Dashboards — composed of tiles that reference saved queries.
 */
export interface DashboardTile {
  id: string;
  saved_query_id: string;
  title?: string | null;
  chart_type: 'table' | 'bar' | 'line' | 'pie' | 'kpi' | string;
}

export interface Dashboard {
  id: string;
  name: string;
  description?: string | null;
  tiles: DashboardTile[];
  created_at: string;
  updated_at: string;
}

export interface DashboardTileInput {
  saved_query_id: string;
  title?: string;
  chart_type?: string;
}

export interface RenderedTile {
  tile_id: string;
  saved_query_id: string;
  title?: string | null;
  chart_type: string;
  status: 'success' | 'error' | 'missing';
  columns: string[];
  rows: Array<Array<unknown>>;
  row_count: number;
  execution_time_ms: number;
  error?: string | null;
}

export interface DashboardRender {
  success: boolean;
  dashboard_id: string;
  rendered_at: string;
  tiles: RenderedTile[];
}

export const dashboardService = {
  async list(): Promise<Dashboard[]> {
    const resp = await client.get<{ success: boolean; dashboards: Dashboard[] }>('/dashboards');
    return resp.dashboards ?? [];
  },
  async get(id: string): Promise<Dashboard> {
    const resp = await client.get<{ success: boolean; dashboard: Dashboard }>(`/dashboards/${encodeURIComponent(id)}`);
    return resp.dashboard;
  },
  async create(payload: { name: string; description?: string; tiles?: DashboardTileInput[] }): Promise<Dashboard> {
    const resp = await client.post<{ success: boolean; dashboard: Dashboard }>('/dashboards', payload);
    return resp.dashboard;
  },
  async update(id: string, patch: { name?: string; description?: string; tiles?: DashboardTileInput[] }): Promise<Dashboard> {
    const resp = await client.patch<{ success: boolean; dashboard: Dashboard }>(`/dashboards/${encodeURIComponent(id)}`, patch);
    return resp.dashboard;
  },
  async remove(id: string): Promise<void> {
    await client.delete<{ success: boolean; id: string }>(`/dashboards/${encodeURIComponent(id)}`);
  },
  async render(id: string): Promise<DashboardRender> {
    return client.post<DashboardRender>(`/dashboards/${encodeURIComponent(id)}/render`);
  },
};

/**
 * Data lineage — walks saved queries via sqlglot on the backend,
 * returns a 3-layer graph (tables → saved queries → dashboards).
 */
export interface LineageNode {
  id: string;
  type: 'table' | 'saved_query' | 'dashboard';
  label: string;
  metadata: Record<string, any>;
}
export interface LineageEdge {
  id: string;
  source: string;
  target: string;
}
export interface LineageGraph {
  success: boolean;
  nodes: LineageNode[];
  edges: LineageEdge[];
  summary: { tables: number; queries: number; dashboards: number; edges: number };
}
export const lineageService = {
  async get(): Promise<LineageGraph> {
    return client.get<LineageGraph>('/lineage');
  },
};

/**
 * LLM cost / token usage — reads in-process Prometheus counter snapshot.
 */
export interface LlmTokenRow {
  provider: string;
  model: string;
  kind: string;
  tokens: number;
}
export interface LlmTokenBreakdown {
  available: boolean;
  rows: LlmTokenRow[];
  totals: { prompt: number; completion: number; cached_completion: number };
}
export const costService = {
  async breakdown(): Promise<LlmTokenBreakdown> {
    return client.get<LlmTokenBreakdown>('/llm-stats');
  },
};

/**
 * Upload Service - File uploads (CSV, Excel, Parquet)
 */
export const uploadService = {
  async uploadFile(file: File, uploadId?: string): Promise<UploadResponse> {
    return client.uploadFile(file, uploadId);
  },

  async getUploadedFiles(): Promise<Array<{ filename: string; size: number; modified: string }>> {
    try {
      const resp = await client.get<{ status: string; files?: Array<{ filename: string; size: number; modified: string }> }>('/files');
      return resp.files || [];
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
// Webhooks (outbound + inbound) Types & Services
// =============================================================================

export interface OutboundWebhook {
  id: string;
  url: string;
  events: string[];
  active: boolean;
  headers: Record<string, string>;
  retries: number;
  description: string;
  created_at: string;
  has_secret: boolean;
}

export interface WebhookDelivery {
  id: string;
  subscription_id: string;
  event_type: string;
  url: string;
  status: 'success' | 'failed';
  http_status: number | null;
  attempts: number;
  error: string | null;
  timestamp: string;
}

export interface InboundHook {
  id: string;
  slug: string;
  kind: 'pipeline' | 'agent';
  target: string;
  active: boolean;
  description: string;
  pass_payload_as: string | null;
  last_fired_at: string | null;
  fire_count: number;
  created_at: string;
  has_secret: boolean;
}

export const webhookService = {
  list: () => client.get<{ status: string; webhooks: OutboundWebhook[] }>('/webhooks'),
  create: (body: {
    url: string; events: string[]; secret?: string;
    headers?: Record<string, string>; retries?: number; description?: string;
  }) => client.post<{ status: string; webhook: OutboundWebhook }>('/webhooks', body),
  update: (id: string, body: Partial<{
    url: string; events: string[]; secret: string;
    headers: Record<string, string>; retries: number;
    active: boolean; description: string;
  }>) => client.patch<{ status: string; webhook: OutboundWebhook }>(`/webhooks/${id}`, body),
  remove: (id: string) => client.delete<{ status: string }>(`/webhooks/${id}`),
  test: (id: string) =>
    client.post<{ status: string; delivery: WebhookDelivery }>(`/webhooks/${id}/test`, {}),
  deliveries: () =>
    client.get<{ status: string; deliveries: WebhookDelivery[] }>('/webhooks/deliveries'),
  events: () => client.get<{ status: string; events: string[] }>('/webhooks/events'),
};

export const inboundHookService = {
  list: () => client.get<{ status: string; hooks: InboundHook[] }>('/hooks'),
  create: (body: {
    slug: string; kind: 'pipeline' | 'agent'; target: string;
    secret?: string; description?: string; pass_payload_as?: string;
  }) => client.post<{ status: string; hook: InboundHook }>('/hooks', body),
  update: (id: string, body: Partial<{
    slug: string; kind: 'pipeline' | 'agent'; target: string;
    secret: string; description: string;
    pass_payload_as: string; active: boolean;
  }>) => client.patch<{ status: string; hook: InboundHook }>(`/hooks/${id}`, body),
  remove: (id: string) => client.delete<{ status: string }>(`/hooks/${id}`),
  fireUrl: (slug: string) => `${API_BASE_URL}/hooks/fire/${encodeURIComponent(slug)}`,
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
