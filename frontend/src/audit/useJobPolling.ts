import { useEffect, useState } from 'react';
import { auditApi } from './auditApi';
import type { JobSnapshot } from './types';

const TERMINAL = new Set(['succeeded', 'failed']);
// A poll that keeps failing is given up on after this many attempts in a row.
const MAX_CONSECUTIVE_FAILURES = 5;

export interface JobPollingState {
  snapshot: JobSnapshot | null;
  /** Set only once polling has STOPPED on an error -- the job will not progress. */
  error: string | null;
}

function httpStatus(e: unknown): number | null {
  const m = /^HTTP (\d{3})/.exec(e instanceof Error ? e.message : String(e));
  return m ? Number(m[1]) : null;
}

function describeFailure(status: number | null): string {
  if (status === 401) return 'Your session has expired. Sign in again to see this audit.';
  if (status === 403) return 'You do not have access to this audit.';
  if (status === 404) return 'This audit job was not found. Jobs do not survive a server restart — run the audit again.';
  return status ? `The audit service is not responding (HTTP ${status}).` : 'The audit service could not be reached.';
}

export function useJobPolling(jobId: string | undefined, intervalMs = 800): JobPollingState {
  const [snapshot, setSnapshot] = useState<JobSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    // BUG-248: `stopped` is per effect run. It used to be one shared ref, so starting a
    // new poll un-stopped the previous job's in-flight loop, which then ran forever.
    let stopped = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout>;
    setSnapshot(null);
    setError(null);

    const tick = async () => {
      try {
        const snap = await auditApi.getJob(jobId);
        if (stopped) return;
        failures = 0;
        setSnapshot(snap);
        if (TERMINAL.has(snap.state)) return; // terminal — stop scheduling
      } catch (e) {
        if (stopped) return;
        failures += 1;
        const status = httpStatus(e);
        // A 4xx will not fix itself (expired session, unknown job); anything else is
        // retried a few times before giving up. Either way polling ends with an error
        // the page can show, instead of retrying silently forever.
        const permanent = status !== null && status >= 400 && status < 500;
        if (permanent || failures >= MAX_CONSECUTIVE_FAILURES) {
          setError(describeFailure(status));
          return;
        }
      }
      timer = setTimeout(tick, intervalMs);
    };

    tick();
    return () => { stopped = true; clearTimeout(timer); };
  }, [jobId, intervalMs]);

  return { snapshot, error };
}
