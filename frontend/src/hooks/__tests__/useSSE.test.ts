import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setAuthToken } from '../../services/api';
import { useSSE } from '../useSSE';

/** A fetch() stand-in that serves text/event-stream bodies the test controls.
 *  Tracks every request so tests can assert on URL, headers and reconnects. */
interface FakeStream {
  url: string;
  headers: Record<string, string>;
  aborted: boolean;
  send: (chunk: string) => void;
  end: () => void;
}

function installFetch(status = 200) {
  const streams: FakeStream[] = [];
  const encoder = new TextEncoder();
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
    const stream: FakeStream = {
      url: String(input),
      headers: (init?.headers ?? {}) as Record<string, string>,
      aborted: false,
      send: (chunk) => controller.enqueue(encoder.encode(chunk)),
      end: () => controller.close(),
    };
    init?.signal?.addEventListener('abort', () => {
      stream.aborted = true;
      try { controller.error(new DOMException('aborted', 'AbortError')); } catch { /* already closed */ }
    });
    streams.push(stream);
    return Promise.resolve({ ok: status === 200, status, body } as unknown as Response);
  });
  vi.stubGlobal('fetch', fetchMock);
  return streams;
}

describe('useSSE', () => {
  beforeEach(() => {
    setAuthToken(null);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    setAuthToken(null);
  });

  it('BUG-109: reconnect() opens a fresh connection after disconnect(), when this hook was the pool\'s only subscriber', () => {
    const streams = installFetch();
    const { result } = renderHook(() => useSSE({ topic: 'bug-109-test-topic' }));

    expect(streams).toHaveLength(1);

    act(() => { result.current.disconnect(); });
    expect(streams[0].aborted).toBe(true);

    act(() => { result.current.reconnect(); });

    // The bug: reconnectTopic() alone does pool.get(topic) and no-ops once
    // disconnect() has deleted the pool entry -- no second connection is
    // ever opened, and the hook is left permanently disconnected.
    expect(streams).toHaveLength(2);
    expect(streams[1].aborted).toBe(false);
  });

  it('sends the bearer token, which a native EventSource could not', () => {
    setAuthToken('tok-123');
    const streams = installFetch();

    renderHook(() => useSSE({ topic: 'auth-topic' }));

    expect(streams[0].headers.Authorization).toBe('Bearer tok-123');
    expect(streams[0].url).toContain('/stream/auth-topic');
  });

  it('delivers typed events with their payload and id', async () => {
    const streams = installFetch();
    const seen: Array<{ type: string; id: string; payload: unknown }> = [];
    const { result } = renderHook(() =>
      useSSE({ topic: 'events-topic', onEvent: (e) => seen.push({ type: e.type, id: e.id, payload: e.payload }) }));

    await waitFor(() => expect(result.current.connected).toBe(true));
    act(() => {
      streams[0].send(': heartbeat comment\n\n');
      streams[0].send('id: e1\nevent: progress\ndata: {"topic":"events-topic","payload":{"percent":40}}\n\n');
      // split across two network chunks
      streams[0].send('id: e2\nevent: complete\ndata: {"payload":{"res');
      streams[0].send('ult":{"rows":3}}}\n\n');
    });

    await waitFor(() => expect(seen).toHaveLength(2));
    expect(seen[0]).toEqual({ type: 'progress', id: 'e1', payload: { percent: 40 } });
    expect(seen[1]).toEqual({ type: 'complete', id: 'e2', payload: { result: { rows: 3 } } });
  });

  it('asks for buffered events on the first connect only when replay is set', () => {
    const streams = installFetch();

    renderHook(() => useSSE({ topic: 'job:1', replay: true }));
    renderHook(() => useSSE({ topic: 'system:health-plain' }));

    expect(streams[0].url).toContain('replay=true');
    expect(streams[1].url).not.toContain('replay');
  });

  it('resumes with a Last-Event-ID header, not a query parameter the gateway ignores', async () => {
    const streams = installFetch();
    const { result } = renderHook(() => useSSE({ topic: 'resume-topic', replay: true, initialBackoff: 10 }));

    await waitFor(() => expect(result.current.connected).toBe(true));
    act(() => { streams[0].send('id: evt-7\nevent: progress\ndata: {"payload":{}}\n\n'); });
    await waitFor(() => expect(result.current.lastEvent?.id).toBe('evt-7'));

    act(() => { streams[0].end(); }); // the connection drops

    await waitFor(() => expect(streams).toHaveLength(2));
    expect(streams[1].headers['Last-Event-ID']).toBe('evt-7');
    expect(streams[1].url).not.toContain('last_event_id');
    expect(streams[1].url).not.toContain('replay');
  });

  it('reports a permanent failure when the stream is refused', async () => {
    installFetch(401);
    const onError = vi.fn();

    const { result } = renderHook(() =>
      useSSE({ topic: 'refused-topic', onError, maxRetries: 0 }));

    await waitFor(() => expect(result.current.error).toBe(true));
    expect(onError).toHaveBeenCalledTimes(1);
    expect(result.current.connected).toBe(false);
  });
});
