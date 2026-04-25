import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Mock useCollabRelay so we control connection state and can dispatch
// inbound frames synchronously from the test. The mock captures the
// caller's onMessage callback and the most-recent send fn into module
// vars that the test reads after each render.
let capturedOnMessage: ((data: ArrayBuffer | string) => void) | undefined;
const sendMock = vi.fn();
let mockConnected = true;

vi.mock('../useCollabRelay', () => ({
  useCollabRelay: (opts: { room: string; onMessage?: (d: ArrayBuffer | string) => void; enabled?: boolean }) => {
    capturedOnMessage = opts.onMessage;
    return { send: sendMock, connected: mockConnected, error: null };
  },
}));

import { usePresence } from '../usePresence';

const STORAGE_KEY = 'aura.collab.identity';

const presenceFrame = (id: string, label = 'Peer', color = '#abcdef') =>
  JSON.stringify({ type: 'presence', id, label, color });

describe('usePresence', () => {
  beforeEach(() => {
    sessionStorage.clear();
    sendMock.mockClear();
    capturedOnMessage = undefined;
    mockConnected = true;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('mints an identity on first mount and persists it to sessionStorage', () => {
    const { result } = renderHook(() => usePresence({ room: 'r1' }));
    expect(result.current.me.id).toBeTruthy();
    expect(result.current.me.label).toMatch(/^Analyst-/);
    const stored = JSON.parse(sessionStorage.getItem(STORAGE_KEY)!);
    expect(stored.id).toBe(result.current.me.id);
  });

  it('reuses a previously stored identity on remount', () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ id: 'fixed-id', label: 'Analyst-XYZW', color: '#123456' }),
    );
    const { result } = renderHook(() => usePresence({ room: 'r1' }));
    expect(result.current.me).toEqual({ id: 'fixed-id', label: 'Analyst-XYZW', color: '#123456' });
  });

  it('sends our hello immediately when the relay is connected', () => {
    renderHook(() => usePresence({ room: 'r1' }));
    expect(sendMock).toHaveBeenCalled();
    const payload = JSON.parse(sendMock.mock.calls[0][0]);
    expect(payload.type).toBe('presence');
  });

  it('adds a peer when a presence frame arrives', () => {
    const { result } = renderHook(() => usePresence({ room: 'r1' }));
    act(() => { capturedOnMessage?.(presenceFrame('peer-1', 'Analyst-AAAA', '#ff0000')); });
    expect(result.current.peers).toHaveLength(1);
    expect(result.current.peers[0]).toMatchObject({ id: 'peer-1', label: 'Analyst-AAAA', color: '#ff0000' });
  });

  it('ignores frames from our own id (no echo into our peer list)', () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ id: 'me', label: 'Analyst-ZZZZ', color: '#000000' }),
    );
    const { result } = renderHook(() => usePresence({ room: 'r1' }));
    act(() => { capturedOnMessage?.(presenceFrame('me', 'Analyst-ZZZZ', '#000000')); });
    expect(result.current.peers).toHaveLength(0);
  });

  it('re-announces ourselves when an unknown peer first appears', () => {
    renderHook(() => usePresence({ room: 'r1' }));
    sendMock.mockClear();
    act(() => { capturedOnMessage?.(presenceFrame('new-peer')); });
    expect(sendMock).toHaveBeenCalledTimes(1);
    // ...but only the first time we see them — repeat frames don't trigger another announce.
    sendMock.mockClear();
    act(() => { capturedOnMessage?.(presenceFrame('new-peer')); });
    expect(sendMock).not.toHaveBeenCalled();
  });

  it('reaps peers that have not been seen for the timeout window', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => usePresence({ room: 'r1' }));
    act(() => { capturedOnMessage?.(presenceFrame('peer-1')); });
    expect(result.current.peers).toHaveLength(1);
    // 20s passes — reaper interval is 5s, timeout is 15s, so this peer is gone.
    act(() => { vi.advanceTimersByTime(20_000); });
    expect(result.current.peers).toHaveLength(0);
  });

  it('clears peers when the room changes', () => {
    const { result, rerender } = renderHook(({ room }) => usePresence({ room }), {
      initialProps: { room: 'r1' },
    });
    act(() => { capturedOnMessage?.(presenceFrame('peer-1')); });
    expect(result.current.peers).toHaveLength(1);
    rerender({ room: 'r2' });
    expect(result.current.peers).toHaveLength(0);
  });

  it('ignores non-presence and malformed frames', () => {
    const { result } = renderHook(() => usePresence({ room: 'r1' }));
    act(() => { capturedOnMessage?.('not json at all'); });
    act(() => { capturedOnMessage?.(JSON.stringify({ type: 'awareness', id: 'p' })); });
    act(() => { capturedOnMessage?.(new ArrayBuffer(8)); });
    expect(result.current.peers).toHaveLength(0);
  });
});
