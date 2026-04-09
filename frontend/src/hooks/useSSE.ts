/**
 * useSSE — Universal Server-Sent Events hook
 *
 * Connects to GET /stream/{topic}, handles reconnection with exponential
 * backoff, and tracks Last-Event-ID for missed-event replay on reconnect.
 *
 * Usage:
 *   const { lastEvent, connected, error } = useSSE({ topic: 'system:health' });
 *   const { lastEvent } = useSSE({ topic: `query:${jobId}`, enabled: !!jobId });
 */
import { useState, useEffect, useRef, useCallback } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

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

  const esRef = useRef<EventSource | null>(null);
  const backoffRef = useRef(initialBackoff);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  const retryCountRef = useRef(0);
  const mountedRef = useRef(true);

  const disconnect = useCallback(() => {
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    if (mountedRef.current) {
      setConnected(false);
    }
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current || !enabled) return;

    // Clean up any existing connection
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    const url = new URL(`${API_BASE}/stream/${encodeURIComponent(topic)}`);
    if (lastEventIdRef.current) {
      url.searchParams.set('last_event_id', lastEventIdRef.current);
    }

    const es = new EventSource(url.toString());
    esRef.current = es;

    es.onopen = () => {
      if (!mountedRef.current) return;
      backoffRef.current = initialBackoff;
      retryCountRef.current = 0;
      setConnected(true);
      setError(false);
      setRetryCount(0);
      onConnect?.();
    };

    // Handle typed events (progress, complete, error, data)
    const eventTypes = ['progress', 'complete', 'error', 'data', 'heartbeat'];
    eventTypes.forEach((evType) => {
      es.addEventListener(evType, (e: MessageEvent) => {
        if (!mountedRef.current) return;
        try {
          const raw = JSON.parse(e.data);
          const sseEvent: SSEEvent = {
            id: e.lastEventId || '',
            type: evType,
            topic: raw.topic ?? topic,
            payload: raw.payload ?? raw,
            timestamp: raw.timestamp ?? new Date().toISOString(),
          };
          if (e.lastEventId) lastEventIdRef.current = e.lastEventId;
          setLastEvent(sseEvent);
          onEvent?.(sseEvent);
        } catch {
          // Ignore parse errors for heartbeats / comments
        }
      });
    });

    // Fallback: unnamed messages
    es.onmessage = (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const raw = JSON.parse(e.data);
        const sseEvent: SSEEvent = {
          id: e.lastEventId || '',
          type: raw.type ?? 'data',
          topic: raw.topic ?? topic,
          payload: raw.payload ?? raw,
          timestamp: raw.timestamp ?? new Date().toISOString(),
        };
        if (e.lastEventId) lastEventIdRef.current = e.lastEventId;
        setLastEvent(sseEvent);
        onEvent?.(sseEvent);
      } catch {
        // ignore
      }
    };

    es.onerror = (e: Event) => {
      if (!mountedRef.current) return;
      es.close();
      esRef.current = null;
      setConnected(false);

      if (retryCountRef.current >= maxRetries) {
        setError(true);
        onError?.(e);
        return;
      }

      const delay = Math.min(backoffRef.current, maxBackoff);
      backoffRef.current = Math.min(backoffRef.current * 2, maxBackoff);
      retryCountRef.current += 1;
      setRetryCount(retryCountRef.current);

      retryTimerRef.current = setTimeout(() => {
        if (mountedRef.current) connect();
      }, delay);
    };
  }, [topic, enabled, initialBackoff, maxBackoff, maxRetries, onConnect, onError, onEvent]);

  const reconnect = useCallback(() => {
    retryCountRef.current = 0;
    backoffRef.current = initialBackoff;
    setError(false);
    setRetryCount(0);
    connect();
  }, [connect, initialBackoff]);

  useEffect(() => {
    mountedRef.current = true;
    if (enabled) connect();
    return () => {
      mountedRef.current = false;
      disconnect();
    };
  }, [topic, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  return { lastEvent, connected, error, retryCount, disconnect, reconnect };
}
