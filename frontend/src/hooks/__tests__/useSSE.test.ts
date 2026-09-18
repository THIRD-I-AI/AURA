import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useSSE } from '../useSSE';

/** Minimal EventSource mock -- jsdom doesn't implement it. Tracks every
 *  instance created so tests can assert whether reconnect() actually opened
 *  a fresh connection or silently no-op'd. */
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  addEventListener() { /* not exercised by this test */ }
  close() { this.closed = true; }
}

describe('BUG-109: useSSE disconnect() then reconnect()', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal('EventSource', MockEventSource);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reconnect() opens a fresh connection after disconnect(), when this hook was the pool\'s only subscriber', () => {
    const { result } = renderHook(() => useSSE({ topic: 'bug-109-test-topic' }));

    expect(MockEventSource.instances).toHaveLength(1);
    const firstConnection = MockEventSource.instances[0];

    act(() => { result.current.disconnect(); });
    expect(firstConnection.closed).toBe(true);

    act(() => { result.current.reconnect(); });

    // The bug: reconnectTopic() alone does pool.get(topic) and no-ops once
    // disconnect() has deleted the pool entry -- no second EventSource is
    // ever created, and the hook is left permanently disconnected.
    expect(MockEventSource.instances).toHaveLength(2);
    expect(MockEventSource.instances[1].closed).toBe(false);
  });
});
