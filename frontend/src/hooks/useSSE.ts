/**
 * useSSE — Universal Server-Sent Events hook
 *
 * Connects to GET /stream/{topic}, handles reconnection with exponential
 * backoff, and tracks Last-Event-ID for missed-event replay on reconnect.
 *
 * Connections are pooled by topic via a module-level manager: multiple
 * components subscribing to the same topic share one EventSource and
 * each receives the same event stream. The connection is closed when
 * the last subscriber unmounts.
 *
 * Usage:
 *   const { lastEvent, connected, error } = useSSE({ topic: 'system:health' });
 *   const { lastEvent } = useSSE({ topic: `query:${jobId}`, enabled: !!jobId });
 */
import { useState, useEffect, useRef, useCallback } from 'react';

const _RAW = (import.meta.env.VITE_API_URL || 'http://localhost:8000') as string;
const API_BASE = `${_RAW.replace(/\/+$/, '')}/api/v1`;

export interface SSEEvent<T = unknown> {
  id: string;
  type: string;  // 'progress' | 'complete' | 'error' | 'data' | 'heartbeat'
  topic: string;
  payload: T;
  timestamp: string;
}

export interface UseSSEOptions {
  /** Topic to subscribe to, e.g. 'system:health' or 'query:job_123' */
  topic: string;
  /** Only open the connection when true (default: true) */
  enabled?: boolean;
  /** Called on every incoming event */
  onEvent?: (event: SSEEvent) => void;
  /** Called when the connection is established */
  onConnect?: () => void;
  /** Called when the connection is permanently lost */
  onError?: (err: Event) => void;
  /** Initial backoff ms (doubles on each retry, capped at maxBackoff) */
  initialBackoff?: number;
  maxBackoff?: number;
  maxRetries?: number;
}

export interface UseSSEReturn {
  lastEvent: SSEEvent | null;
  connected: boolean;
  error: boolean;
  retryCount: number;
  disconnect: () => void;
  reconnect: () => void;
}

// ─────────────────────────────────────────────────────────────────
// Module-level connection pool: one EventSource per topic
// ─────────────────────────────────────────────────────────────────

interface PoolSubscriber {
  onEvent?: (e: SSEEvent) => void;
  onConnect?: () => void;
  onError?: (e: Event) => void;
  setConnected: (v: boolean) => void;
  setError: (v: boolean) => void;
  setRetryCount: (n: number) => void;
  setLastEvent: (e: SSEEvent) => void;
}

interface PoolEntry {
  es: EventSource | null;
  subscribers: Set<PoolSubscriber>;
  lastEventId: string | null;
  backoff: number;
  retryCount: number;
  retryTimer: ReturnType<typeof setTimeout> | null;
  initialBackoff: number;
  maxBackoff: number;
  maxRetries: number;
  connected: boolean;
}

const pool = new Map<string, PoolEntry>();

function openConnection(topic: string, entry: PoolEntry) {
  if (entry.es) {
    entry.es.close();
    entry.es = null;
  }

  const url = new URL(`${API_BASE}/stream/${encodeURIComponent(topic)}`);
  if (entry.lastEventId) url.searchParams.set('last_event_id', entry.lastEventId);

  const es = new EventSource(url.toString());
  entry.es = es;

  es.onopen = () => {
    entry.backoff = entry.initialBackoff;
    entry.retryCount = 0;
    entry.connected = true;
    entry.subscribers.forEach((s) => {
      s.setConnected(true);
      s.setError(false);
      s.setRetryCount(0);
      s.onConnect?.();
    });
  };

  const dispatch = (sseEvent: SSEEvent) => {
    if (sseEvent.id) entry.lastEventId = sseEvent.id;
    entry.subscribers.forEach((s) => {
      s.setLastEvent(sseEvent);
      s.onEvent?.(sseEvent);
    });
  };

  const eventTypes = ['progress', 'complete', 'error', 'data', 'heartbeat'];
  eventTypes.forEach((evType) => {
    es.addEventListener(evType, (e: MessageEvent) => {
      try {
        const raw = JSON.parse(e.data);
        dispatch({
          id: e.lastEventId || '',
          type: evType,
          topic: raw.topic ?? topic,
          payload: raw.payload ?? raw,
          timestamp: raw.timestamp ?? new Date().toISOString(),
        });
      } catch {
        // ignore parse errors (heartbeats / comments)
      }
    });
  });

  es.onmessage = (e: MessageEvent) => {
    try {
      const raw = JSON.parse(e.data);
      dispatch({
        id: e.lastEventId || '',
        type: raw.type ?? 'data',
        topic: raw.topic ?? topic,
        payload: raw.payload ?? raw,
        timestamp: raw.timestamp ?? new Date().toISOString(),
      });
    } catch {
      // ignore
    }
  };

  es.onerror = (e: Event) => {
    es.close();
    entry.es = null;
    entry.connected = false;
    entry.subscribers.forEach((s) => s.setConnected(false));

    if (entry.retryCount >= entry.maxRetries) {
      entry.subscribers.forEach((s) => {
        s.setError(true);
        s.onError?.(e);
      });
      return;
    }

    const delay = Math.min(entry.backoff, entry.maxBackoff);
    entry.backoff = Math.min(entry.backoff * 2, entry.maxBackoff);
    entry.retryCount += 1;
    entry.subscribers.forEach((s) => s.setRetryCount(entry.retryCount));

    entry.retryTimer = setTimeout(() => {
      if (entry.subscribers.size > 0) openConnection(topic, entry);
    }, delay);
  };
}

function subscribe(topic: string, sub: PoolSubscriber, opts: {
  initialBackoff: number;
  maxBackoff: number;
  maxRetries: number;
}): () => void {
  let entry = pool.get(topic);
  if (!entry) {
    entry = {
      es: null,
      subscribers: new Set(),
      lastEventId: null,
      backoff: opts.initialBackoff,
      retryCount: 0,
      retryTimer: null,
      initialBackoff: opts.initialBackoff,
      maxBackoff: opts.maxBackoff,
      maxRetries: opts.maxRetries,
      connected: false,
    };
    pool.set(topic, entry);
  }
  entry.subscribers.add(sub);

  // First subscriber opens the connection; later ones inherit current state.
  if (!entry.es && entry.subscribers.size === 1) {
    openConnection(topic, entry);
  } else if (entry.connected) {
    sub.setConnected(true);
  }

  return () => {
    const e = pool.get(topic);
    if (!e) return;
    e.subscribers.delete(sub);
    if (e.subscribers.size === 0) {
      if (e.retryTimer) {
        clearTimeout(e.retryTimer);
        e.retryTimer = null;
      }
      if (e.es) {
        e.es.close();
        e.es = null;
      }
      pool.delete(topic);
    }
  };
}

function reconnectTopic(topic: string) {
  const entry = pool.get(topic);
  if (!entry) return;
  if (entry.retryTimer) {
    clearTimeout(entry.retryTimer);
    entry.retryTimer = null;
  }
  entry.retryCount = 0;
  entry.backoff = entry.initialBackoff;
  entry.subscribers.forEach((s) => {
    s.setError(false);
    s.setRetryCount(0);
  });
  openConnection(topic, entry);
}

// ─────────────────────────────────────────────────────────────────
// Hook
// ─────────────────────────────────────────────────────────────────

export function useSSE({
  topic,
  enabled = true,
  onEvent,
  onConnect,
  onError,
  initialBackoff = 1000,
  maxBackoff = 30000,
  maxRetries = 10,
}: UseSSEOptions): UseSSEReturn {
  const [lastEvent, setLastEvent] = useState<SSEEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(false);
  const [retryCount, setRetryCount] = useState(0);

  // Keep callbacks in a ref so the subscribe effect doesn't re-run on every render.
  const cbRef = useRef({ onEvent, onConnect, onError });
  cbRef.current = { onEvent, onConnect, onError };

  const unsubscribeRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!enabled) {
      setConnected(false);
      return;
    }

    const sub: PoolSubscriber = {
      onEvent: (e) => cbRef.current.onEvent?.(e),
      onConnect: () => cbRef.current.onConnect?.(),
      onError: (e) => cbRef.current.onError?.(e),
      setConnected,
      setError,
      setRetryCount,
      setLastEvent,
    };

    const unsub = subscribe(topic, sub, { initialBackoff, maxBackoff, maxRetries });
    unsubscribeRef.current = unsub;

    return () => {
      unsub();
      unsubscribeRef.current = null;
    };
  }, [topic, enabled, initialBackoff, maxBackoff, maxRetries]);

  const disconnect = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    setConnected(false);
  }, []);

  const reconnect = useCallback(() => {
    reconnectTopic(topic);
  }, [topic]);

  return { lastEvent, connected, error, retryCount, disconnect, reconnect };
}
