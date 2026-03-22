/**
 * useAgentExecutor — React hook for the AURA Agentic DE framework
 * Supports: /agent/plan, /agent/execute, /agent/execute/stream (SSE)
 */
import { useState, useCallback, useRef } from 'react';

const API_BASE = localStorage.getItem('apiUrl') || 'http://localhost:8000';

// ── Types ────────────────────────────────────────────────────────────

export interface AgentTask {
  id: string;
  task_type: string;
  description: string;
  agent_name: string;
  depends_on: string[];
  status?: string;
}

export interface AgentPlan {
  plan_id: string;
  summary: string;
  tasks: AgentTask[];
}

export interface AgentTaskResult {
  status: string;
  output: Record<string, unknown> | null;
  suggestions: string[];
  artifacts: string[];
  error: string | null;
  duration_ms: number;
}

export interface AgentReport {
  success: boolean;
  summary: string;
  duration_ms: number;
  tasks: Record<string, AgentTaskResult>;
  skipped: string[];
}

export interface AgentProgress {
  agent: string;
  message: string;
  progress: number;
}

export type AgentPhase = 'idle' | 'planning' | 'executing' | 'streaming' | 'done' | 'error';

export interface AgentExecutorState {
  phase: AgentPhase;
  plan: AgentPlan | null;
  report: AgentReport | null;
  progress: AgentProgress[];
  error: string | null;
}

export interface AgentExecutorActions {
  /** Generate a plan without executing */
  planOnly: (prompt: string, opts?: AgentOpts) => Promise<AgentPlan | null>;
  /** Plan + execute synchronously */
  execute: (prompt: string, opts?: AgentOpts) => Promise<AgentReport | null>;
  /** Plan + execute with SSE progress stream */
  stream: (prompt: string, opts?: AgentOpts) => Promise<void>;
  /** Reset state */
  reset: () => void;
  /** Abort a running stream */
  abort: () => void;
}

interface AgentOpts {
  files?: string[];
  connection?: Record<string, unknown>;
  schema_context?: Record<string, unknown>;
  execute_sql?: boolean;
}

// ── Hook ─────────────────────────────────────────────────────────────

export function useAgentExecutor(): [AgentExecutorState, AgentExecutorActions] {
  const [phase, setPhase] = useState<AgentPhase>('idle');
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [report, setReport] = useState<AgentReport | null>(null);
  const [progress, setProgress] = useState<AgentProgress[]>([]);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const phaseRef = useRef<AgentPhase>('idle');

  // Keep phaseRef in sync with the state value
  const updatePhase = useCallback((next: AgentPhase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  const reset = useCallback(() => {
    updatePhase('idle');
    setPlan(null);
    setReport(null);
    setProgress([]);
    setError(null);
    abortRef.current?.abort();
    abortRef.current = null;
  }, [updatePhase]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    updatePhase('idle');
  }, [updatePhase]);

  const buildBody = (prompt: string, opts?: AgentOpts) => ({
    prompt,
    files: opts?.files ?? [],
    connection: opts?.connection ?? null,
    schema_context: opts?.schema_context ?? null,
    execute_sql: opts?.execute_sql ?? false,
  });

  // ── Plan only ────────────────────────────────────────────────────
  const planOnly = useCallback(async (prompt: string, opts?: AgentOpts): Promise<AgentPlan | null> => {
    reset();
    updatePhase('planning');
    try {
      const res = await fetch(`${API_BASE}/agent/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildBody(prompt, opts)),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(errBody.detail || res.statusText);
      }
      const data: AgentPlan = await res.json();
      setPlan(data);
      updatePhase('done');
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      updatePhase('error');
      return null;
    }
  }, [reset, updatePhase]);

  // ── Execute (sync) ──────────────────────────────────────────────
  const execute = useCallback(async (prompt: string, opts?: AgentOpts): Promise<AgentReport | null> => {
    reset();
    updatePhase('executing');
    try {
      const res = await fetch(`${API_BASE}/agent/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildBody(prompt, opts)),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(errBody.detail || res.statusText);
      }
      const data: AgentReport = await res.json();
      setReport(data);
      updatePhase('done');
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      updatePhase('error');
      return null;
    }
  }, [reset, updatePhase]);

  // ── Stream (SSE) ────────────────────────────────────────────────
  const stream = useCallback(async (prompt: string, opts?: AgentOpts): Promise<void> => {
    reset();
    updatePhase('streaming');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(`${API_BASE}/agent/execute/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildBody(prompt, opts)),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        const errBody = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(errBody.detail || res.statusText);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const json = line.slice(6).trim();
          if (!json) continue;

          try {
            const event = JSON.parse(json);

            if (event.error) {
              setError(event.error);
              updatePhase('error');
              return;
            }
            if (event.done && event.report) {
              setReport(event.report);
              updatePhase('done');
              return;
            }
            // Progress event
            setProgress((prev) => [...prev, event as AgentProgress]);
          } catch {
            // skip malformed SSE
          }
        }
      }

      // Stream ended without explicit done — use ref to avoid stale closure
      if (phaseRef.current !== 'done' && phaseRef.current !== 'error') {
        updatePhase('done');
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      updatePhase('error');
    }
  }, [reset, updatePhase]);

  const state: AgentExecutorState = { phase, plan, report, progress, error };
  const actions: AgentExecutorActions = { planOnly, execute, stream, reset, abort };

  return [state, actions];
}
