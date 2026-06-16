import { useEffect, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { auditApi } from './auditApi';
import { AuthNav } from '../auth/AuthNav';
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
      <AuthNav />
      <h1 className="aud-hero__title">Cryptographically-verifiable compliance audits</h1>
      <p className="aud-hero__sub">
        Pick a regulated-decision scenario. Watch the audit run. Get a signed
        certificate anyone can verify.
      </p>
      <div data-testid="aud-trust-band" className="aud-trust">
        <span aria-hidden="true">⬢</span>
        Every result is ED25519-signed and independently verifiable.
      </div>

      {error && (
        <div data-testid="scenarios-error" className="aud-scenario">
          Couldn't load scenarios.{' '}
          <button className="ui-btn ui-btn--secondary ui-btn--sm" onClick={load}>Retry</button>
        </div>
      )}

      {!scenarios && !error && <p className="aud-scenario__desc">Loading scenarios…</p>}

      <div className="aud-scenarios">
        {scenarios?.map((s) => (
          <button
            key={s.id}
            data-testid={`scenario-card-${s.id}`}
            className="aud-scenario"
            onClick={() => run(s.id)}
            disabled={launching !== null}
          >
            <span className="aud-scenario__vertical">{s.vertical}</span>
            <h3 className="aud-scenario__title">{s.title}</h3>
            <p className="aud-scenario__desc">{s.description}</p>
            <span className="aud-scenario__cta">{launching === s.id ? 'Starting…' : 'Run audit →'}</span>
          </button>
        ))}
      </div>

      <p className="aud-links">
        <Link to="/audit/new" className="aud-link">Run a custom audit</Link>
        <span aria-hidden="true">·</span>
        {/* Hard nav: intentionally exits the public shell to load the dashboard. */}
        <a href="/app" className="aud-link aud-link--muted">Open dashboard</a>
      </p>
    </div>
  );
}
