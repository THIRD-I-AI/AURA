import { useEffect, useRef, useState } from 'react';
import { auditApi } from './auditApi';
import type { JobSnapshot } from './types';

const TERMINAL = new Set(['succeeded', 'failed']);

export interface JobPollingState {
  snapshot: JobSnapshot | null;
  error: string | null;
}

export function useJobPolling(jobId: string | undefined, intervalMs = 800): JobPollingState {
  const [snapshot, setSnapshot] = useState<JobSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stopped = useRef(false);

  useEffect(() => {
    if (!jobId) return;
    stopped.current = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const snap = await auditApi.getJob(jobId);
        if (stopped.current) return;
        setSnapshot(snap);
        if (TERMINAL.has(snap.state)) return; // terminal — stop scheduling
      } catch (e) {
        if (stopped.current) return;
        setError(e instanceof Error ? e.message : String(e));
      }
      timer = setTimeout(tick, intervalMs);
    };

    tick();
    return () => { stopped.current = true; clearTimeout(timer); };
  }, [jobId, intervalMs]);

  return { snapshot, error };
}
