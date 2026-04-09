/**
 * AURA Global State Store
 * ========================
 * Custom React Context + useReducer implementation.
 * Zero third-party dependencies — 100% AURA-owned code.
 *
 * Provides shared state for data that lives across multiple pages:
 * health, files, connections, dashboard stats, query history.
 *
 * Usage:
 *   // Wrap your app once:
 *   <AuraProvider><App /></AuraProvider>
 *
 *   // Use in any component:
 *   const { state, actions } = useAuraStore();
 *   const { stats, files } = state;
 *   actions.fetchStats();
 */

import {
  createContext,
  useContext,
  useReducer,
  useCallback,
  useMemo,
  type ReactNode,
} from 'react';
import {
  analyticsService,
  type DashboardStats,
  type HealthStatus,
} from '../services/api';

// ═══════════════════════════════════════════════════════════════════
//  Types
// ═══════════════════════════════════════════════════════════════════

export interface UploadedFile {
  id: string;
  name: string;
  filename?: string;
  sizeBytes: number;
  rows: number;
  columns: number;
  columnNames: string[];
  uploadedAt: string | null;
  status: 'ready' | 'uploaded';
  file?: { name?: string; size?: number; type?: string };
  response?: Record<string, any>;
}

export interface Connection {
  id: string;
  name: string;
  type: string;
  host?: string;
  port?: number;
  database?: string;
  is_active: boolean;
  table_count?: number;
  created_at?: string;
}

export interface AuraState {
  health: HealthStatus | null;
  stats: DashboardStats | null;
  statsLoading: boolean;
  statsError: string | null;
  files: UploadedFile[];
  connections: Connection[];
  connectionsLoading: boolean;
  queryHistory: any[];
  queryHistoryLoading: boolean;
}

// ═══════════════════════════════════════════════════════════════════
//  Reducer
// ═══════════════════════════════════════════════════════════════════

type Action =
  | { type: 'SET_HEALTH'; payload: HealthStatus }
  | { type: 'STATS_LOADING' }
  | { type: 'STATS_LOADED'; payload: DashboardStats }
  | { type: 'STATS_ERROR'; payload: string }
  | { type: 'SET_FILES'; payload: UploadedFile[] }
  | { type: 'ADD_FILE'; payload: UploadedFile }
  | { type: 'CONNECTIONS_LOADING' }
  | { type: 'CONNECTIONS_LOADED'; payload: Connection[] }
  | { type: 'QUERY_HISTORY_LOADING' }
  | { type: 'QUERY_HISTORY_LOADED'; payload: any[] };

const initialState: AuraState = {
  health: null,
  stats: null,
  statsLoading: false,
  statsError: null,
  files: [],
  connections: [],
  connectionsLoading: false,
  queryHistory: [],
  queryHistoryLoading: false,
};

function reducer(state: AuraState, action: Action): AuraState {
  switch (action.type) {
    case 'SET_HEALTH':
      return { ...state, health: action.payload };
    case 'STATS_LOADING':
      return { ...state, statsLoading: true, statsError: null };
    case 'STATS_LOADED':
      return { ...state, stats: action.payload, statsLoading: false };
    case 'STATS_ERROR':
      return { ...state, statsLoading: false, statsError: action.payload };
    case 'SET_FILES':
      return { ...state, files: action.payload };
    case 'ADD_FILE':
      return { ...state, files: [...state.files, action.payload] };
    case 'CONNECTIONS_LOADING':
      return { ...state, connectionsLoading: true };
    case 'CONNECTIONS_LOADED':
      return { ...state, connections: action.payload, connectionsLoading: false };
    case 'QUERY_HISTORY_LOADING':
      return { ...state, queryHistoryLoading: true };
    case 'QUERY_HISTORY_LOADED':
      return { ...state, queryHistory: action.payload, queryHistoryLoading: false };
    default:
      return state;
  }
}

// ═══════════════════════════════════════════════════════════════════
//  Helpers
// ═══════════════════════════════════════════════════════════════════

function parseUploadsFromStorage(): UploadedFile[] {
  const raw = localStorage.getItem('recentUploads');
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return parsed.map((f: any) => ({
      id: f.response?.file_id || f.id || `AURA-${Math.random().toString(36).substr(2, 5).toUpperCase()}`,
      name: f.file?.name || f.response?.filename || f.name || 'Untitled dataset',
      sizeBytes: f.file?.size || f.size || 0,
      rows:
        typeof f.response?.rows === 'number' && f.response.rows > 0
          ? f.response.rows
          : typeof f.rows === 'number' && f.rows > 0
            ? f.rows
            : 0,
      columns: Array.isArray(f.response?.columns) ? f.response.columns.length : typeof f.columns === 'number' ? f.columns : 0,
      columnNames: Array.isArray(f.response?.columns) ? f.response.columns : [],
      uploadedAt: f.uploadedAt || null,
      status: (typeof f.response?.rows === 'number' && f.response.rows > 0) ? 'ready' as const : 'uploaded' as const,
      file: f.file,
      response: f.response,
    }));
  } catch {
    return [];
  }
}

const API_BASE = () =>
  localStorage.getItem('apiUrl') ||
  import.meta.env.VITE_API_URL ||
  'http://localhost:8000';

// ═══════════════════════════════════════════════════════════════════
//  Context + Provider
// ═══════════════════════════════════════════════════════════════════

interface AuraActions {
  setHealth: (h: HealthStatus) => void;
  fetchStats: () => Promise<void>;
  loadFilesFromStorage: () => void;
  addFile: (file: UploadedFile) => void;
  fetchConnections: () => Promise<void>;
  fetchQueryHistory: (limit?: number, statusFilter?: string) => Promise<void>;
}

interface AuraContextValue {
  state: AuraState;
  actions: AuraActions;
}

const AuraContext = createContext<AuraContextValue | null>(null);

export function AuraProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  // ── Actions ─────────────────────────────────────────────────────

  const setHealth = useCallback((h: HealthStatus) => {
    dispatch({ type: 'SET_HEALTH', payload: h });
  }, []);

  const fetchStats = useCallback(async () => {
    if (state.statsLoading) return;
    dispatch({ type: 'STATS_LOADING' });
    try {
      const data = await analyticsService.getDashboardStats();
      dispatch({ type: 'STATS_LOADED', payload: data });
    } catch {
      dispatch({ type: 'STATS_ERROR', payload: 'Backend services offline' });
    }
  }, [state.statsLoading]);

  const loadFilesFromStorage = useCallback(() => {
    dispatch({ type: 'SET_FILES', payload: parseUploadsFromStorage() });
  }, []);

  const addFile = useCallback((file: UploadedFile) => {
    dispatch({ type: 'ADD_FILE', payload: file });
  }, []);

  const fetchConnections = useCallback(async () => {
    if (state.connectionsLoading) return;
    dispatch({ type: 'CONNECTIONS_LOADING' });
    try {
      const resp = await fetch(`${API_BASE()}/connections`);
      const data = await resp.json();
      if (data.success) {
        dispatch({ type: 'CONNECTIONS_LOADED', payload: data.connections });
      } else {
        dispatch({ type: 'CONNECTIONS_LOADED', payload: [] });
      }
    } catch {
      dispatch({ type: 'CONNECTIONS_LOADED', payload: [] });
    }
  }, [state.connectionsLoading]);

  const fetchQueryHistory = useCallback(async (limit = 100, statusFilter?: string) => {
    if (state.queryHistoryLoading) return;
    dispatch({ type: 'QUERY_HISTORY_LOADING' });
    try {
      const resp = await analyticsService.getQueryHistory(limit, statusFilter);
      if (resp.success && resp.queries.length > 0) {
        dispatch({ type: 'QUERY_HISTORY_LOADED', payload: resp.queries });
      } else {
        const saved = localStorage.getItem('queryHistory');
        dispatch({ type: 'QUERY_HISTORY_LOADED', payload: saved ? JSON.parse(saved) : [] });
      }
    } catch {
      const saved = localStorage.getItem('queryHistory');
      dispatch({ type: 'QUERY_HISTORY_LOADED', payload: saved ? JSON.parse(saved) : [] });
    }
  }, [state.queryHistoryLoading]);

  // ── Memoize context value ───────────────────────────────────────

  const actions = useMemo<AuraActions>(
    () => ({ setHealth, fetchStats, loadFilesFromStorage, addFile, fetchConnections, fetchQueryHistory }),
    [setHealth, fetchStats, loadFilesFromStorage, addFile, fetchConnections, fetchQueryHistory],
  );

  const value = useMemo<AuraContextValue>(
    () => ({ state, actions }),
    [state, actions],
  );

  return <AuraContext.Provider value={value}>{children}</AuraContext.Provider>;
}

// ═══════════════════════════════════════════════════════════════════
//  Hook
// ═══════════════════════════════════════════════════════════════════

export function useAuraStore(): AuraContextValue {
  const ctx = useContext(AuraContext);
  if (!ctx) {
    throw new Error('useAuraStore must be used within <AuraProvider>');
  }
  return ctx;
}
