import { useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useJobPolling } from './useJobPolling';
import type { Estimate } from './types';

function EstimatorRow({ e }: { e: Estimate }) {
  const done = e.point_estimate !== undefined || e.error !== undefined;
  return (
    <div data-testid={`estimator-${e.method}`} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-3) 0', borderBottom: '1px solid var(--border-default)' }}>
      <span style={{ width: 14, height: 14, borderRadius: '50%', background: e.error ? 'var(--red)' : done ? 'var(--green)' : 'var(--accent)', flexShrink: 0 }} />
      <span style={{ flex: 1, fontWeight: 500 }}>{e.method}</span>
      <span style={{ fontFamily: 'monospace', fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>
        {e.error ? 'n/a' : e.point_estimate !== undefined ? `${e.point_estimate.toFixed(3)} [${e.ci_low?.toFixed(2)}, ${e.ci_high?.toFixed(2)}]` : 'running…'}
      </span>
    </div>
  );
}

export function AuditProgress() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const { snapshot } = useJobPolling(jobId, 800);

  useEffect(() => {
    if (snapshot?.state === 'succeeded' && snapshot.artifact) {
      navigate(`/certificate/${snapshot.artifact.audit_record_hash}`);
    }
  }, [snapshot, navigate]);

  if (snapshot?.state === 'failed') {
    return <div data-testid="audit-failed" style={{ padding: 'var(--space-6)' }}>
      <h2>Audit could not complete</h2>
      <p style={{ color: 'var(--text-tertiary)' }}>{snapshot.error}</p>
      <button onClick={() => navigate('/')}>Back to scenarios</button>
    </div>;
  }

  const estimates = snapshot?.artifact?.estimates ?? [];

  return (
    <div data-testid="audit-progress">
      <h2>Running audit…</h2>
      <p style={{ color: 'var(--text-tertiary)' }}>State: {snapshot?.state ?? 'queued'}</p>
      <div>{estimates.map((e) => <EstimatorRow key={e.method} e={e} />)}</div>
      {estimates.length === 0 && <p>Spinning up estimators…</p>}
    </div>
  );
}
