import { describe, expect, it } from 'vitest';
import { parseSSEBuffer } from '../api';

describe('parseSSEBuffer', () => {
  it('parses complete event frames and returns the remainder', () => {
    const buf =
      'event: tool_call\ndata: {"name":"run_sql"}\n\n' +
      'event: text\ndata: {"text":"hi"}\n\n' +
      'event: done\ndata: {"reason":"st';
    const { events, rest } = parseSSEBuffer(buf);
    expect(events).toEqual([
      { event: 'tool_call', data: { name: 'run_sql' } },
      { event: 'text', data: { text: 'hi' } },
    ]);
    expect(rest).toBe('event: done\ndata: {"reason":"st');
  });

  it('ignores heartbeat comment lines', () => {
    const { events } = parseSSEBuffer(': heartbeat\n\nevent: done\ndata: {"reason":"stop"}\n\n');
    expect(events).toEqual([{ event: 'done', data: { reason: 'stop' } }]);
  });

  it('skips frames whose data is not valid JSON', () => {
    const { events, rest } = parseSSEBuffer('event: text\ndata: not-json\n\n');
    expect(events).toEqual([]);
    expect(rest).toBe('');
  });
});
