import type React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import type { PipelineTelemetry } from '../usePipelineTelemetry';

// BUG-028: UasrRecoveries fired GET /uasr/recovery/pending from render-body
// `if (!loaded) { void load(); }` instead of a mount-only useEffect — the
// parent re-renders on every ~8s telemetry tick, so any re-render landing
// before the async fetch resolved re-fired a duplicate request. This test
// forces exactly that: a telemetry re-render happens while `load()` is still
// pending, and asserts the fetch still only fires once.
const TELEMETRY: PipelineTelemetry = {
  serviceStatus: { uasr: 'healthy', api_gateway: 'healthy' },
  overall: 'healthy',
  healthyServices: 2,
  totalServices: 8,
  huScore: 0.9,
  pipelines: [],
  events: [],
  connected: true,
  lastUpdate: new Date(0).toISOString(),
};

let telemetryTick = 0;
vi.mock('../usePipelineTelemetry', async (importActual) => {
  const actual = await importActual<typeof import('../usePipelineTelemetry')>();
  return {
    ...actual,
    // A fresh telemetry object identity every call, mimicking the real hook's
    // ~8s tick — this is what makes the parent (and UasrRecoveries) re-render.
    usePipelineTelemetry: () => ({ ...TELEMETRY, lastUpdate: String(telemetryTick++) }),
  };
});

const { pendingMock, getResolvePending } = vi.hoisted(() => {
  let resolvePending: (v: unknown[]) => void = () => {};
  return {
    pendingMock: vi.fn(() => new Promise((resolve) => { resolvePending = resolve; })),
    getResolvePending: () => resolvePending,
  };
});

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    streamingService: { start: vi.fn(), stop: vi.fn(), pause: vi.fn(), resume: vi.fn() },
    healingService: { pending: pendingMock, approve: vi.fn(), reject: vi.fn() },
  };
});

import PipelinePanel from '../../panels/PipelinePanel';
import { healingService } from '../../../services/api';

const props = { api: {}, params: {}, containerApi: {} } as unknown as React.ComponentProps<
  typeof PipelinePanel
>;

describe('UasrRecoveries mount-only fetch (BUG-028)', () => {
  it('fires GET /uasr/recovery/pending exactly once even across a re-render before it resolves', async () => {
    const { rerender } = render(<PipelinePanel {...props} />);

    // Select the UASR node to mount UasrRecoveries and trigger its fetch.
    fireEvent.click(screen.getByRole('button', { name: /UASR Self-Heal/i }));
    expect(pendingMock).toHaveBeenCalledTimes(1);

    // Force a parent re-render (the telemetry tick) while load() is still
    // in flight — the bug re-fired the fetch here.
    await act(async () => {
      rerender(<PipelinePanel {...props} />);
    });
    expect(pendingMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      getResolvePending()([]);
      await Promise.resolve();
    });

    // A further re-render after resolution must not fire another fetch either.
    await act(async () => {
      rerender(<PipelinePanel {...props} />);
    });
    expect(pendingMock).toHaveBeenCalledTimes(1);
  });
});

// BUG-253: approve/reject signed an override on a single click, and a failed
// decision was swallowed.
describe('UasrRecoveries decisions need a confirming click (BUG-253)', () => {
  async function openWithOneRecovery() {
    render(<PipelinePanel {...props} />);
    fireEvent.click(screen.getByRole('button', { name: /UASR Self-Heal/i }));
    await act(async () => {
      getResolvePending()([{ id: 'rec-1', source_id: 'orders', diagnosis: 'schema drift', status: 'pending' }]);
      await Promise.resolve();
    });
  }

  it('the first click arms, the second commits', async () => {
    vi.mocked(healingService.approve).mockClear();
    await openWithOneRecovery();
    fireEvent.click(screen.getByRole('button', { name: 'approve' }));
    expect(healingService.approve).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'confirm approve' })); });
    expect(healingService.approve).toHaveBeenCalledWith('rec-1', 'operator', undefined);
  });

  it('cancel disarms, and a failed decision is shown', async () => {
    vi.mocked(healingService.reject).mockClear();
    vi.mocked(healingService.reject).mockRejectedValueOnce(new Error('down'));
    await openWithOneRecovery();
    fireEvent.click(screen.getByRole('button', { name: 'reject' }));
    fireEvent.click(screen.getByRole('button', { name: 'cancel' }));
    expect(healingService.reject).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'reject' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'confirm reject' })); });
    expect(screen.getByRole('alert')).toHaveTextContent('could not reject recovery rec-1');
  });
});
