/**
 * A Server-Sent Events client built on fetch().
 *
 * The browser's EventSource cannot set request headers. Every stream route is
 * behind the JWT gate, which reads only the Authorization header, so a native
 * EventSource always got 401 once auth was on. This reads the same
 * text/event-stream format from a fetch() body, which can carry the bearer
 * token and a Last-Event-ID header.
 *
 * It exposes the part of the EventSource surface useSSE relies on: onopen,
 * onerror, onmessage, addEventListener and close. It does not reconnect by
 * itself; useSSE owns backoff and retry.
 */

export interface StreamMessage {
  data: string;
  lastEventId: string;
}

type Listener = (e: StreamMessage) => void;

export class FetchEventSource {
  onopen: (() => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onmessage: Listener | null = null;

  private readonly listeners = new Map<string, Listener[]>();
  private readonly controller = new AbortController();
  private closed = false;

  constructor(url: string, headers: Record<string, string> = {}) {
    void this.run(url, headers);
  }

  addEventListener(type: string, listener: Listener): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  close(): void {
    this.closed = true;
    this.controller.abort();
  }

  private fail(): void {
    if (this.closed) return;
    this.closed = true;
    this.controller.abort();
    this.onerror?.(new Event('error'));
  }

  private async run(url: string, headers: Record<string, string>): Promise<void> {
    try {
      const resp = await fetch(url, {
        headers: { Accept: 'text/event-stream', ...headers },
        signal: this.controller.signal,
        cache: 'no-store',
      });
      if (!resp.ok || !resp.body) {
        this.fail();
        return;
      }
      if (this.closed) return;
      this.onopen?.();

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, '\n');
        let end = buffer.indexOf('\n\n');
        while (end !== -1) {
          this.dispatchBlock(buffer.slice(0, end));
          buffer = buffer.slice(end + 2);
          end = buffer.indexOf('\n\n');
        }
      }
      // The server closed the stream: the caller decides whether to reconnect.
      this.fail();
    } catch {
      this.fail();
    }
  }

  private dispatchBlock(block: string): void {
    if (this.closed) return;
    let type = 'message';
    let id = '';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue; // blank, or a comment/heartbeat
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
      if (field === 'event') type = value;
      else if (field === 'data') data.push(value);
      else if (field === 'id') id = value;
    }
    if (data.length === 0) return;
    const message: StreamMessage = { data: data.join('\n'), lastEventId: id };
    if (type === 'message') this.onmessage?.(message);
    this.listeners.get(type)?.forEach((l) => l(message));
  }
}
