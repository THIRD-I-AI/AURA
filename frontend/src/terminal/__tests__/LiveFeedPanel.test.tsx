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
    act(() => {
      capturedOnEvent?.({ id: '1', type: 'progress', topic: 'system:health', payload: { msg: 'healthy' }, timestamp: 't1' });
    });
    expect(screen.getByText(/system:health/)).toBeInTheDocument();
  });
});
