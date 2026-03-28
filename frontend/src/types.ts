// =============================================================================
// AURA Enterprise Type System
// =============================================================================

export interface DataResult {
  columns: string[];
  rows: any[][];
  error?: string;
}

// ── Connections / Data Sources ──────────────────────────────────────

export interface Connection {
  id: string;
  name: string;
  type: 'postgresql' | 'mysql' | 'bigquery' | 'sqlite' | 'csv' | 'duckdb';
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  ssl?: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_tested?: string | null;
  table_count: number;
}

export interface ConnectionCreatePayload {
  name: string;
  type: string;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  password?: string;
  ssl?: boolean;
  extra?: Record<string, any>;
}

// ── Query History ──────────────────────────────────────────────────

export interface QueryRecord {
  id: string;
  prompt: string;
  sql: string;
  status: 'success' | 'error' | 'pending';
  rows: number;
  executionTime: number;
  timestamp: string;
}

export interface QueryHistoryResponse {
  success: boolean;
  queries: QueryRecord[];
  total: number;
}

// ── Dashboard ──────────────────────────────────────────────────────

export interface DashboardStats {
  total_rows: number;
  active_sources: number;
  total_connections: number;
  file_sources: number;
  queries_run: number;
  system_health: 'healthy' | 'degraded' | 'down';
  uptime_percentage: number;
}

// ── Chat ───────────────────────────────────────────────────────────

export interface ChatMessage {
  id: string;
  type: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  metadata?: Record<string, any>;
}

// ── Jobs ───────────────────────────────────────────────────────────

export interface Job {
  id: string;
  status: 'pending' | 'approved' | 'running' | 'completed' | 'cancelled' | 'error';
  sql?: string;
  created_at: string;
  approved_at?: string;
  cancelled_at?: string;
}

// ── File / Upload ──────────────────────────────────────────────────

export interface UploadedFile {
  id: string;
  name: string;
  size: number;
  type: string;
  uploaded_at: string;
  row_count?: number;
  column_count?: number;
}

// ── Schema ─────────────────────────────────────────────────────────

export interface TableSchema {
  [tableName: string]: string[];
}

export interface ColumnSchema {
  name: string;
  type: string;
  nullable?: boolean;
}
