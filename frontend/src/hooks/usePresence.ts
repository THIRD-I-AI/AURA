/**
 * usePresence — lightweight presence layer over the collab relay.
 *
 * Each client emits a small JSON heartbeat (`{type:'presence', id, label,
 * color}`) every few seconds. Peers timestamp the last heartbeat per id
 * and reap entries that go silent. When we first hear from an unknown
 * peer we re-send our own hello so late joiners populate quickly without
 * waiting for the next tick.
 *
 * Identity is per browser tab (sessionStorage) so two tabs in the same
 * browser appear as distinct viewers — which is what users expect.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useCollabRelay } from './useCollabRelay';

export interface PresenceIdentity {
  id: string;
  label: string;
  color: string;
}

export interface PresencePeer extends PresenceIdentity {
  lastSeen: number;
}

export interface UsePresenceOptions {
  room: string;
  enabled?: boolean;
}

export interface UsePresenceReturn {
  me: PresenceIdentity;
  peers: PresencePeer[];
  connected: boolean;
}

const STORAGE_KEY = 'aura.collab.identity';
const HEARTBEAT_MS = 5_000;
const TIMEOUT_MS = 15_000;
const REAP_MS = 5_000;
const PALETTE = [
  '#60a5fa', '#34d399', '#fbbf24', '#f87171',
  '#a78bfa', '#fb923c', '#22d3ee', '#f472b6',
];

function shortId(): string {
  return Math.floor(Math.random() * 36 ** 4)
    .toString(36)
    .toUpperCase()
    .padStart(4, '0');
}

function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function loadOrCreateIdentity(): PresenceIdentity {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<PresenceIdentity>;
      if (parsed && typeof parsed.id === 'string' && typeof parsed.label === 'string' && typeof parsed.color === 'string') {
        return { id: parsed.id, label: parsed.label, color: parsed.color };
      }
    }
  } catch { /* sessionStorage may be blocked or contain garbage */ }
  const ident: PresenceIdentity = {
    id: newId(),
    label: `Analyst-${shortId()}`,
    color: PALETTE[Math.floor(Math.random() * PALETTE.length)],
  };
  try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(ident)); } catch { /* ignore */ }
  return ident;
}

interface PresenceMessage {
  type: 'presence';
  id: string;
  label: string;
  color: string;
}

function parsePresence(data: ArrayBuffer | string): PresenceMessage | null {
  if (typeof data !== 'string') return null;
  let parsed: unknown;
  try { parsed = JSON.parse(data); } catch { return null; }
  if (!parsed || typeof parsed !== 'object') return null;
  const m = parsed as Record<string, unknown>;
  if (m.type !== 'presence' || typeof m.id !== 'string') return null;
  return {
    type: 'presence',
    id: m.id,
    label: typeof m.label === 'string' ? m.label : 'Anonymous',
    color: typeof m.color === 'string' ? m.color : '#9ca3af',
  };
}

export function usePresence({ room, enabled = true }: UsePresenceOptions): UsePresenceReturn {
  const meRef = useRef<PresenceIdentity | null>(null);
  if (!meRef.current) meRef.current = loadOrCreateIdentity();
  const me = meRef.current;

  const [peers, setPeers] = useState<Map<string, PresencePeer>>(() => new Map());
  const peersRef = useRef(peers);
  peersRef.current = peers;

  const sendRef = useRef<((data: string) => void) | null>(null);

  const helloPayload = useMemo(
    () => JSON.stringify({ type: 'presence', id: me.id, label: me.label, color: me.color }),
    [me],
  );

  const onMessage = useCallback((data: ArrayBuffer | string) => {
    const msg = parsePresence(data);
    if (!msg || msg.id === me.id) return;
    const isNew = !peersRef.current.has(msg.id);
    setPeers((prev) => {
      const next = new Map(prev);
      next.set(msg.id, { ...msg, lastSeen: Date.now() });
      return next;
    });
    if (isNew && sendRef.current) sendRef.current(helloPayload);
  }, [me.id, helloPayload]);

  const { send, connected } = useCollabRelay({ room, enabled, onMessage });
  sendRef.current = send;

  // Announce on connect + heartbeat. Re-runs on room change because the
  // relay re-establishes a fresh socket.
  useEffect(() => {
    if (!enabled || !connected) return;
    send(helloPayload);
    const tick = setInterval(() => send(helloPayload), HEARTBEAT_MS);
    return () => clearInterval(tick);
  }, [enabled, connected, helloPayload, send]);

  // Reap silent peers.
  useEffect(() => {
    const interval = setInterval(() => {
      const cutoff = Date.now() - TIMEOUT_MS;
      setPeers((prev) => {
        let changed = false;
        const next = new Map(prev);
        for (const [id, p] of next) {
          if (p.lastSeen < cutoff) { next.delete(id); changed = true; }
        }
        return changed ? next : prev;
      });
    }, REAP_MS);
    return () => clearInterval(interval);
  }, []);

  // Drop peers from the previous room when the caller switches rooms.
  useEffect(() => {
    setPeers(new Map());
  }, [room]);

  const peerList = useMemo(
    () => Array.from(peers.values()).sort((a, b) => a.label.localeCompare(b.label)),
    [peers],
  );

  return { me, peers: peerList, connected };
}
