import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

import { useJobPolling } from '../useJobPolling';
import { auditApi } from '../auditApi';

describe('useJobPolling', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it('polls until a terminal state then stops', async () => {
    const getJob = vi.spyOn(auditApi, 'getJob')
      .mockResolvedValueOnce({ job_id: 'j', state: 'running' })
      .mockResolvedValueOnce({ job_id: 'j', state: 'succeeded', artifact: { audit_record_hash: 'h', estimates: [], refutations: [], signature_status: 'ok', signing_key_source: 'persisted_file' } });

    const { result } = renderHook(() => useJobPolling('j', 800));

    await waitFor(() => expect(result.current.snapshot?.state).toBe('running'));
    await vi.advanceTimersByTimeAsync(800);
    await waitFor(() => expect(result.current.snapshot?.state).toBe('succeeded'));

    const callsAtDone = getJob.mock.calls.length;
    await vi.advanceTimersByTimeAsync(2000);
    expect(getJob.mock.calls.length).toBe(callsAtDone); // stopped polling
  });

  it('exposes failed state', async () => {
    vi.spyOn(auditApi, 'getJob').mockResolvedValue({ job_id: 'j', state: 'failed', error: 'nope' });
    const { result } = renderHook(() => useJobPolling('j', 800));
    await waitFor(() => expect(result.current.snapshot?.state).toBe('failed'));
    expect(result.current.snapshot?.error).toBe('nope');
  });

  // BUG-248: every error used to be retried every 800ms forever, with nothing shown.
  it('stops polling on a 4xx and reports it', async () => {
    const getJob = vi.spyOn(auditApi, 'getJob').mockRejectedValue(new Error('HTTP 404: {"detail":"job j not found"}'));
    const { result } = renderHook(() => useJobPolling('j', 800));
    await waitFor(() => expect(result.current.error).toMatch(/not found/));
    await vi.advanceTimersByTimeAsync(5000);
    expect(getJob).toHaveBeenCalledTimes(1);
  });

  it('retries a transient failure, recovers, and only gives up after repeated failures', async () => {
    const getJob = vi.spyOn(auditApi, 'getJob')
      .mockRejectedValueOnce(new Error('HTTP 502: bad gateway'))
      .mockResolvedValueOnce({ job_id: 'j', state: 'running' })
      .mockRejectedValue(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => useJobPolling('j', 800));
    await vi.advanceTimersByTimeAsync(800);
    await waitFor(() => expect(result.current.snapshot?.state).toBe('running'));
    expect(result.current.error).toBeNull();
    await vi.advanceTimersByTimeAsync(800 * 6);
    await waitFor(() => expect(result.current.error).toMatch(/could not be reached/));
    const calls = getJob.mock.calls.length;
    expect(calls).toBe(7); // 1 failure + 1 success + 5 consecutive failures
    await vi.advanceTimersByTimeAsync(5000);
    expect(getJob.mock.calls.length).toBe(calls);
  });

  it('the in-flight poll of a previous job does not resume after the job id changes', async () => {
    let releaseOld: (v: { job_id: string; state: 'running' }) => void = () => undefined;
    const getJob = vi.spyOn(auditApi, 'getJob').mockImplementation((id: string) =>
      id === 'old'
        ? new Promise((res) => { releaseOld = res; })
        : Promise.resolve({ job_id: 'new', state: 'succeeded' as const }),
    );
    const { result, rerender } = renderHook(({ id }) => useJobPolling(id, 800), { initialProps: { id: 'old' } });
    rerender({ id: 'new' });
    await waitFor(() => expect(result.current.snapshot?.job_id).toBe('new'));
    releaseOld({ job_id: 'old', state: 'running' });
    await vi.advanceTimersByTimeAsync(5000);
    expect(result.current.snapshot?.job_id).toBe('new');
    expect(getJob.mock.calls.filter(([id]) => id === 'old')).toHaveLength(1);
  });
});
