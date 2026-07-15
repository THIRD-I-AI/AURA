/**
 * useCollabRelay — bare WebSocket client for the /ws/collab/{room} relay.
 *
 * S6.10 v1 ships only the transport layer. Layer Yjs on top by piping
 * `send` and the message callback into a `y-websocket`-compatible adapter
 * once an editor surface (CodeMirror / Monaco) lands. The relay forwards
 * bytes verbatim so Yjs binary frames pass through unchanged.
 *
 * Why not import `y-websocket` directly: that package picks the editor
 * binding for us (Monaco vs CodeMirror vs ProseMirror) and the dep is
 * heavy. Keeping the hook protocol-agnostic lets v2 choose freely.
 *
 * Usage:
 *   const { send, connected } = useCollabRelay({
 *     room: `dashboard:${id}`,
 *     onMessage: (data) => yDoc.applyUpdate(data),
 *   });
 */
import { useCallback, useEffect, useRef, useState } from 'react';

// Same-origin by default. An unset (or empty) VITE_API_URL -- the production
// build behind nginx -- derives the ws(s) origin from window.location, so the
// handshake targets whatever host served the app (cloud-agnostic, no rebuild).
// Dev sets VITE_API_URL=http://localhost:8000 explicitly (cross-origin dev
// server). The relay sits at root, not under /api/v1, because browsers can't
// send auth headers on WS handshakes (see api_gateway/main.py).
const _WS_ENV = (import.meta.env.VITE_API_URL as string | undefined) || '';
const _sameOriginWs = (): string =>
  typeof window !== 'undefined'
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
    : 'ws://localhost:8000';
const WS_BASE = _WS_ENV
  ? _WS_ENV.replace(/\/+$/, '').replace(/^http/, 'ws')
  : _sameOriginWs();

export interface UseCollabRelayOptions {
  /** Room id — clients in the same room exchange messages. */
  room: string;
  /** Called for every inbound frame from a peer. */
  onMessage?: (data: ArrayBuffer | string) => void;
  /** Skip connecting (useful while the room id is still loading). */
  enabled?: boolean;
  /** Cap on reconnect backoff in ms (default 30000). */
  maxBackoffMs?: number;
}

export interface UseCollabRelayReturn {
  /** Send a frame to every other peer in the room. No-op when disconnected. */
  send: (data: ArrayBuffer | Uint8Array | string) => void;
  /** True while the underlying WebSocket is OPEN. */
  connected: boolean;
  /** Last connection error, if any. */
  error: Error | null;
}

export function useCollabRelay(opts: UseCollabRelayOptions): UseCollabRelayReturn {
  const { room, onMessage, enabled = true, maxBackoffMs = 30_000 } = opts;
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);

  // Keep a stable ref to the latest callback so the connect effect
  // doesn't tear down the socket every render.
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    if (!enabled || !room) return;
    let cancelled = false;
    let attempt = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (cancelled) return;
      const url = `${WS_BASE}/ws/collab/${encodeURIComponent(room)}`;
      const ws = new WebSocket(url);
      ws.binaryType = 'arraybuffer';
      socketRef.current = ws;

      ws.onopen = () => {
        attempt = 0;
        setConnected(true);
        setError(null);
      };
      ws.onmessage = (ev) => {
        onMessageRef.current?.(ev.data);
      };
      ws.onerror = () => {
        // Close handler runs next; capture a generic error here so the
        // caller can surface a message before reconnect kicks in.
        setError(new Error('collab websocket error'));
      };
      ws.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        const delay = Math.min(maxBackoffMs, 500 * 2 ** attempt);
        attempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      const ws = socketRef.current;
      socketRef.current = null;
      if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.close();
      }
    };
  }, [room, enabled, maxBackoffMs]);

  const send = useCallback((data: ArrayBuffer | Uint8Array | string) => {
    const ws = socketRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(data as ArrayBuffer);
  }, []);

  return { send, connected, error };
}
