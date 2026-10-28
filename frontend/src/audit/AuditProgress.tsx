import { useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useJobPolling } from './useJobPolling';
import type { Estimate } from './types';
import { Button } from '@/components/ui-kit/button';

/** Coerce a contract numeric (number on live path, string on replay) to fixed-dp. */
function fmt(v: number | string | undefined, dp: number): string {
  if (v === undefined || v === null) return '—';
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(dp) : String(v);
}

function EstimatorRow({ e }: { e: Estimate }) {
  const done = (e.point !== undefined && e.point !== null) || e.error != null;
  const dot = e.error ? 'error' : done ? 'done' : 'running';
  return (
    <div data-testid={`estimator-${e.method}`} className="aud-estimator">
      <span className={`aud-estimator__dot aud-estimator__dot--${dot}`} />
      <span className="aud-estimator__method">{e.method}</span>
      <span className="aud-estimator__value">
        {e.error ? 'n/a' : (e.point !== undefined && e.point !== null)
          ? `${fmt(e.point, 3)} [${fmt(e.ci_lower, 2)}, ${fmt(e.ci_upper, 2)}]`
          : 'running…'}
      </span>
    </div>
  );
}

export function AuditProgress() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const { snapshot, error } = useJobPolling(jobId, 800);

  useEffect(() => {
    if (snapshot?.state === 'succeeded' && snapshot.artifact) {
      navigate(`/certificate/${snapshot.artifact.audit_record_hash}`);
    }
  }, [snapshot, navigate]);

  if (snapshot?.state === 'failed') {
    return (
      <div data-testid="audit-failed">
        <h1 className="aud-title">Audit could not complete</h1>
        <p className="aud-scenario__desc">{snapshot.error}</p>
        <Button variant="outline" onClick={() => navigate('/')}>Back to scenarios</Button>
      </div>
    );
  }

  // BUG-248: polling gave up (expired session, unknown job, service down). Without this
  // the page sat on "Running audit…" forever.
  if (error) {
    return (
      <div data-testid="audit-poll-error" role="alert">
        <h1 className="aud-title">Audit status unavailable</h1>
        <p className="aud-scenario__desc">{error}</p>
        <Button variant="outline" onClick={() => navigate('/')}>Back to scenarios</Button>
      </div>
    );
  }

  const estimates = snapshot?.artifact?.estimates ?? [];
  const state = snapshot?.state ?? 'queued';
  const stages: Array<{ key: string; label: string }> = [
    { key: 'queued', label: 'queued' },
    { key: 'running', label: 'estimating' },
    { key: 'succeeded', label: 'signing' },
  ];
  const reached = (k: string) =>
    (state === 'running' && k !== 'succeeded') ||
    (state === 'succeeded') ||
    (state === 'queued' && k === 'queued');

  return (
    <div data-testid="audit-progress">
      <h1 className="aud-title">Running audit…</h1>
      <div data-testid="aud-stages" className="aud-progress__stages">
        {stages.map((s) => {
          const cls = reached(s.key)
            ? state === 'succeeded' ? 'aud-stage aud-stage--done' : 'aud-stage aud-stage--active'
            : 'aud-stage';
          return (
            <span key={s.key} className={cls}>
              {s.label}
            </span>
          );
        })}
      </div>
      <div>{estimates.map((e) => <EstimatorRow key={e.method} e={e} />)}</div>
      {estimates.length === 0 && <p className="aud-scenario__desc">Spinning up estimators…</p>}
    </div>
  );
}
