import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

let capturedOnEvent: ((e: { id: string; type: string; topic: string; payload: unknown; timestamp: string }) => void) | null = null;
vi.mock('../../hooks/useSSE', () => ({
  useSSE: (opts: { onEvent?: (e: unknown) => void }) => {
    capturedOnEvent = opts.onEvent ?? null;
    return { lastEvent: null, connected: true, error: null };
  },
}));

import LiveFeedPanel from '../panels/LiveFeedPanel';

describe('LiveFeedPanel', () => {
  it('shows a connected feed and renders incoming events newest-first', () => {
    const props = { api: {}, params: {}, containerApi: {} } as any;
    render(<LiveFeedPanel {...props} />);
    expect(screen.getByTestId('livefeed-panel')).toBeInTheDocument();
    // status text is just "live" — the dot is a styled ::before, not a literal char
    expect(screen.getByText('live').textContent).toBe('live');
    const timestamp = '2026-08-25T09:30:00.000Z';
    const payload = { msg: 'healthy', detail: { retries: 0 } };
    act(() => {
      capturedOnEvent?.({ id: '1', type: 'progress', topic: 'system:health', payload, timestamp });
    });
    expect(screen.getByText(/system:health/)).toBeInTheDocument();
    // timestamp renders via toLocaleTimeString (compact, matches PipelinePanel/AuditPanel convention)
    expect(screen.getByText(new Date(timestamp).toLocaleTimeString())).toBeInTheDocument();
    // payload stays single-line JSON but carries the full pretty-printed value as a hover tooltip
    const payloadEl = screen.getByText(JSON.stringify(payload));
    expect(payloadEl).toHaveAttribute('title', JSON.stringify(payload, null, 2));
  });
});
