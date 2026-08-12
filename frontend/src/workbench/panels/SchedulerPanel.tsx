/* Scheduler — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Real scheduled jobs from
   GET /saved-queries (filtered to schedule != null) + real run history from
   GET /saved-queries/:id/runs, via savedQueryService — this is the gateway's
   in-process saved-query scheduler (api_gateway/main.py starts it at boot;
   api_gateway/routers/queries.py `_scheduler_loop` fires due queries against
   DuckDB and records each run). The standalone scheduler_service/ (distributed
   LISTEN/NOTIFY queue, port 8004) has no router mounted on the gateway — main.py
   only pings its /health for the service list — so nothing from it is reachable
   from this frontend; this panel is honest about only the saved-query scheduler.
   Pause/Resume reuses the verified PUT .../schedule endpoint (same schedule
   payload with `enabled` flipped); Remove reuses the verified DELETE endpoint. */
import { useCallback, useEffect, useState } from 'react';
import { Pause, Play, RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { savedQueryService, type SavedQuery, type SavedQueryRun } from '../../services/api';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function describeSchedule(q: SavedQuery): string {
  const s = q.schedule;
  if (!s) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  const at = `${pad(s.hour)}:${pad(s.minute)} UTC`;
  if (s.interval === 'hourly') return `hourly at :${pad(s.minute)}`;
  if (s.interval === 'daily') return `daily at ${at}`;
  if (s.interval === 'weekly') return `weekly on ${DAYS[s.day_of_week ?? 0] ?? s.day_of_week} at ${at}`;
  return s.interval;
}

export default function SchedulerPanel() {
  const [items, setItems] = useState<SavedQuery[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, SavedQueryRun[]>>({});
  const [runsError, setRunsError] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      const list = await savedQueryService.list();
      setItems((list ?? []).filter((q) => q.schedule != null));
      setError(null);
    } catch {
      setError('Could not reach the gateway to load scheduled jobs.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggleOpen = useCallback(async (id: string) => {
    if (openId === id) { setOpenId(null); return; }
    setOpenId(id);
    if (!runs[id] && !runsError[id]) {
      try {
        const r = await savedQueryService.listRuns(id);
        setRuns((prev) => ({ ...prev, [id]: r }));
      } catch {
        setRunsError((prev) => ({ ...prev, [id]: 'Could not load run history for this job.' }));
      }
    }
  }, [openId, runs, runsError]);

  const toggleEnabled = useCallback(async (q: SavedQuery) => {
    if (!q.schedule) return;
    setBusy(q.id);
    try {
      await savedQueryService.setSchedule(q.id, { ...q.schedule, enabled: !q.schedule.enabled });
      await load();
    } catch {
      setError(`Could not update the schedule for "${q.name}".`);
    } finally {
      setBusy(null);
    }
  }, [load]);

  const removeSchedule = useCallback(async (q: SavedQuery) => {
    setBusy(q.id);
    try {
      await savedQueryService.clearSchedule(q.id);
      await load();
    } catch {
      setError(`Could not remove the schedule for "${q.name}".`);
    } finally {
      setBusy(null);
    }
  }, [load]);

  const count = items?.length ?? 0;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-scheduler-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {items === null ? 'loading…' : `${count} scheduled job${count === 1 ? '' : 's'} · saved-query scheduler`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <Panel>
        {items === null && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading scheduled jobs…</div>}
        {items !== null && count === 0 && !error && (
          <EmptyState
            intent="empty"
            title="No scheduled jobs"
            description="Attach a schedule (hourly, daily, or weekly) to a saved query in the Library and it runs here automatically."
          />
        )}
        {(items ?? []).map((q, i) => {
          const enabled = q.schedule?.enabled ?? false;
          const open = openId === q.id;
          const rowRuns = runs[q.id];
          return (
            <div key={q.id} className={cn('flex flex-col', i > 0 && 'border-t border-border')}>
              <div className="flex items-center gap-2.5 px-4 py-3">
                <span className={cn('size-1.5 shrink-0', enabled ? 'bg-signal' : 'bg-text-tertiary')} />
                <span className="truncate text-sm font-semibold text-card-foreground">{q.name || '(untitled)'}</span>
                <div className="flex-1" />
                <span className="font-mono text-2xs text-text-tertiary">{describeSchedule(q)}</span>
                <span className={cn('font-mono text-2xs font-bold tracking-wider', enabled ? 'text-signal' : 'text-text-tertiary')}>
                  {enabled ? 'ENABLED' : 'PAUSED'}
                </span>
              </div>
              {(q.next_run_at || q.last_run_at) && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 pb-2 font-mono text-2xs text-text-tertiary">
                  {q.next_run_at && <span>next {new Date(q.next_run_at).toLocaleString()}</span>}
                  {q.last_run_at && <span>last {new Date(q.last_run_at).toLocaleString()}</span>}
                </div>
              )}
              <div className="flex gap-2 px-4 pb-3">
                <Button size="xs" variant="outline" onClick={() => toggleEnabled(q)} disabled={busy === q.id}>
                  {enabled ? <><Pause /> Pause</> : <><Play /> Resume</>}
                </Button>
                <Button size="xs" variant="outline" className="text-danger" onClick={() => removeSchedule(q)} disabled={busy === q.id}>
                  Remove schedule
                </Button>
                <Button size="xs" variant="ghost" onClick={() => toggleOpen(q.id)}>
                  {open ? 'Hide runs' : 'Show runs'}
                </Button>
              </div>
              {open && (
                <div className="border-t border-border bg-secondary px-4 py-2.5" data-testid={`wb-scheduler-runs-${q.id}`}>
                  {runsError[q.id] && <div className="font-mono text-2xs text-danger">{runsError[q.id]}</div>}
                  {!runsError[q.id] && !rowRuns && <div className="font-mono text-2xs text-text-tertiary">Loading runs…</div>}
                  {!runsError[q.id] && rowRuns && rowRuns.length === 0 && (
                    <div className="font-mono text-2xs text-text-tertiary">No runs yet.</div>
                  )}
                  {(rowRuns ?? []).map((r) => (
                    <div key={r.id} className="flex items-center gap-2.5 py-1">
                      <span className={cn('size-1.5 shrink-0', r.status === 'success' ? 'bg-signal' : 'bg-danger')} />
                      <span className="font-mono text-2xs text-text-secondary">{new Date(r.started_at).toLocaleString()}</span>
                      <span className={cn('font-mono text-2xs font-bold tracking-wider', r.status === 'success' ? 'text-signal' : 'text-danger')}>
                        {r.status.toUpperCase()}
                      </span>
                      <span className="font-mono text-2xs text-text-tertiary">{r.row_count} rows · {r.execution_time_ms}ms</span>
                      {r.error && <span className="truncate font-mono text-2xs text-danger">{r.error}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </Panel>
    </div>
  );
}
