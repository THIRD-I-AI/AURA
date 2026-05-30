import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { auditApi } from './auditApi';
import type { Scenario } from './types';

export function AuditFrontDoor() {
  const navigate = useNavigate();
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [launching, setLaunching] = useState<string | null>(null);

  const load = () => {
    setError(null);
    auditApi.listScenarios().then(setScenarios).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(load, []);

  const run = async (id: string) => {
    setLaunching(id);
    try {
      const { job_id } = await auditApi.runScenario(id);
      navigate(`/audit/${job_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLaunching(null);
    }
  };

  return (
    <div data-testid="audit-front-door">
      <h1 style={{ fontSize: 'var(--font-3xl)', fontWeight: 700, letterSpacing: '-0.03em' }}>
        Cryptographically-verifiable compliance audits
      </h1>
      <p style={{ color: 'var(--text-tertiary)', marginBottom: 'var(--space-8)' }}>
        Pick a regulated-decision scenario. Watch the audit run. Get a signed certificate anyone can verify.
      </p>

      {error && (
        <div data-testid="scenarios-error" style={{ padding: 'var(--space-5)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-lg)' }}>
          Couldn't load scenarios. <button onClick={load}>Retry</button>
        </div>
      )}

      {!scenarios && !error && <p>Loading scenarios…</p>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 'var(--space-5)' }}>
        {scenarios?.map((s) => (
          <button
            key={s.id}
            data-testid={`scenario-card-${s.id}`}
            onClick={() => run(s.id)}
            disabled={launching !== null}
            style={{ textAlign: 'left', background: 'var(--bg-surface)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)', cursor: 'pointer' }}
          >
            <span style={{ fontSize: 'var(--font-xs)', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-tertiary)' }}>{s.vertical}</span>
            <h3 style={{ margin: 'var(--space-2) 0' }}>{s.title}</h3>
            <p style={{ fontSize: 'var(--font-sm)', color: 'var(--text-secondary)' }}>{s.description}</p>
            <span style={{ fontSize: 'var(--font-sm)', color: 'var(--accent)' }}>{launching === s.id ? 'Starting…' : 'Run audit →'}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
