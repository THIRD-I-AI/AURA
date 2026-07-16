import { useEffect, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { auditApi } from './auditApi';
import { AuthNav } from '../auth/AuthNav';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { Button } from '@/components/ui-kit/button';
import type { Scenario } from './types';

export function AuditFrontDoor({ embedded = false }: { embedded?: boolean } = {}) {
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
      {!embedded && <AuthNav />}
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
        <EmptyState
          data-testid="scenarios-error"
          intent="error"
          title="Couldn't load scenarios"
          description="The audit gateway didn't respond. Your connection or the service may be down."
          action={
            <Button variant="outline" size="sm" onClick={load}>
              Retry
            </Button>
          }
          className="my-6 min-h-[160px] border border-border bg-secondary"
        />
      )}

      {!scenarios && !error && (
        <EmptyState
          data-testid="scenarios-loading"
          intent="awaiting"
          title="Loading scenarios"
          description="Fetching the available regulated-decision scenarios…"
          className="my-6 min-h-[160px]"
        />
      )}

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

      {/* Capability band: always renders, so the page states its value even
          when the scenario list is empty or the gateway is unreachable. */}
      <div className="aud-caps" aria-label="Platform capabilities">
        {[
          { k: 'Signed & replayable', t: 'Every conclusion is ED25519-signed and deterministically replayable — auditors re-run the exact decision, not a screenshot of it.' },
          { k: 'Self-healing data', t: 'Pipelines detect drift and repair themselves (MAPE-K) across NetSuite, Workday, and Kafka — every human override signed to a WORM log.' },
          { k: 'Fail-closed by design', t: 'PII perimeter masking, tamper-evident audit ledger, and fail-closed auth. Nothing crosses the boundary unsigned or unmasked.' },
        ].map((c) => (
          <div key={c.k} className="aud-cap">
            <span className="aud-cap__k">{c.k}</span>
            <p className="aud-cap__t">{c.t}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
