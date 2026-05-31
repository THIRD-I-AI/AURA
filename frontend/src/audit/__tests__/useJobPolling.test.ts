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
});
