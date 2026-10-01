import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SSEEvent } from '../../hooks/useSSE';

let emit: (ev: SSEEvent) => void = () => undefined;
vi.mock('../../hooks/useSSE', () => ({
  useSSE: (opts: { onEvent: (ev: SSEEvent) => void }) => { emit = opts.onEvent; },
}));

import PipelineMonitor from '../PipelineMonitor';

// BUG-250: only a successful `complete` with a result reached the parent, so a run
// that failed over SSE left PipelinesPanel's Execute/Preview disabled on "Running…".
describe('BUG-250: PipelineMonitor reports every terminal event', () => {
  afterEach(() => vi.clearAllMocks());

  it('calls onSettled when the run errors, and shows the error', () => {
    const onSettled = vi.fn();
    const onComplete = vi.fn();
    render(<PipelineMonitor runId="r1" onComplete={onComplete} onSettled={onSettled} />);
    act(() => emit({ type: 'error', payload: { error: 'source table missing' } } as SSEEvent));
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();
    expect(screen.getByText('source table missing')).toBeInTheDocument();
  });

  it('calls onSettled on completion, with or without a result', () => {
    const onSettled = vi.fn();
    const onComplete = vi.fn();
    render(<PipelineMonitor runId="r1" onComplete={onComplete} onSettled={onSettled} />);
    act(() => emit({ type: 'complete', payload: {} } as SSEEvent));
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();
  });
});
