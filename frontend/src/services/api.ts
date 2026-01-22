/**
 * Production-Grade API Client for AURA Analytics
 * Centralized API communication layer with typed responses and error handling
 */

// =============================================================================
// Configuration
// =============================================================================

const API_BASE_URL = 'http://localhost:8000'; // Direct backend connection with CORS
const REQUEST_TIMEOUT = 30000; // 30 seconds
const HEALTH_CHECK_INTERVAL = 10000; // 10 seconds for faster detection

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
  status: 'Success' | 'Fallback' | 'Error';
  final_query?: string;
  error_message?: string;
  metadata?: {
    execution_time_ms?: number;
    rows_affected?: number;
  };
}

export interface ExecutionResult {
  success: boolean;
  data?: Array<Record<string, any>>;
  columns?: string[];
  row_count?: number;
  execution_time_ms?: number;
  error?: string;
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
  async uploadFile(file: File): Promise<any> {
    const TARGET_URL = 'http://localhost:8000/upload';
    console.log("🚀 STARTING UPLOAD TO:", TARGET_URL);
    
    const formData = new FormData();
    formData.append('file', file); // Use 'file' as the key
    
    try {
      const response = await fetch(TARGET_URL, {
        method: 'POST',
        body: formData,
        // NO HEADERS defined here (Browser handles it)
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
    } catch (error) {
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
 * Chat Service - Natural language query generation
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
    return client.post<QueryResponse>('/generate_query', {
      session_id: context?.sessionId || `session_${Date.now()}`,
      prompt: message,
      context: context?.uploadedFile
        ? `File: ${context.uploadedFile}\nColumns: ${context.columns?.join(', ')}`
        : undefined,
      uploaded_file: context?.uploadedFile || null,
      columns: context?.columns || null,
    });
  },

  async getChatHistory(sessionId: string): Promise<ChatMessage[]> {
    try {
      return client.get<ChatMessage[]>(`/chat/history/${sessionId}`);
    } catch (error) {
      // History endpoint may not exist yet, return empty
      return [];
    }
  },
};

/**
 * Connector Service - Database connections management
 */
export const connectorService = {
  async listSources(): Promise<DataSource[]> {
    return client.get<DataSource[]>('/connections');
  },

  async registerSource(credentials: ConnectionCredentials): Promise<DataSource> {
    return client.post<DataSource>('/connections', credentials);
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
      return client.get<string[]>('/supported-databases');
    } catch {
      return ['postgresql', 'mysql', 'sqlite'];
    }
  },

  async getSchema(connectionId: string): Promise<Record<string, string[]>> {
    return client.get<Record<string, string[]>>(`/connections/${connectionId}/schema`);
  },
};

/**
 * Analytics Service - Dashboard metrics and insights
 */
export const analyticsService = {
  async getDashboardStats(): Promise<DashboardStats> {
    try {
      const [sources, health] = await Promise.all([
        connectorService.listSources(),
        client.checkHealth(),
      ]);

      // TODO: Replace with real backend endpoint when available
      return {
        total_rows: 0, // Will be populated by metadata service
        active_sources: sources.filter((s) => s.is_active).length,
        queries_run: 0, // Will be populated by execution logs
        system_health: health.status,
        uptime_percentage: health.status === 'healthy' ? 99.9 : 85.0,
      };
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
      query,
      connection_id: connectionId,
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
  async uploadFile(file: File): Promise<UploadResponse> {
    return client.uploadFile(file);
  },

  async getUploadedFiles(): Promise<Array<{ id: string; name: string; uploaded_at: string }>> {
    try {
      return client.get<Array<{ id: string; name: string; uploaded_at: string }>>('/uploads');
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
// Exports
// =============================================================================

export { ApiClient };
export default client;
