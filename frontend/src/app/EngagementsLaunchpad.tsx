import { Link } from 'react-router-dom';
import type { PageType } from '../components/Layout/AppLayout';
import './engagements.css';

/**
 * The auditor-workbench home launchpad. There is no "list my audit runs"
 * backend endpoint, so this presents real entry points into the audit
 * lifecycle rather than fabricating a run table — honest by construction.
 * In-app destinations go through onNavigate (URL-routed); the public audit
 * wizard is a normal link.
 */
export function EngagementsLaunchpad({ onNavigate }: { onNavigate: (page: PageType) => void }) {
  return (
    <section data-testid="engagements-launchpad" className="eng-launchpad">
      <div className="eng-launchpad__intro">
        <h2 className="eng-launchpad__title">Start an engagement</h2>
        <p className="eng-launchpad__sub">
          Run a causal audit, review flagged findings, or audit your own dataset —
          every result is ED25519-signed and independently verifiable.
        </p>
      </div>
      <div className="eng-launchpad__grid">
        <button data-testid="launch-counterfactual" className="eng-tile" onClick={() => onNavigate('counterfactual')}>
          <span className="eng-tile__kicker">Causal audit</span>
          <span className="eng-tile__title">Run a counterfactual audit</span>
          <span className="eng-tile__desc">Four estimators, refutation tests, adversarial review, hash-sealed artifact.</span>
          <span className="eng-tile__cta">Open →</span>
        </button>
        <button data-testid="launch-exceptions" className="eng-tile" onClick={() => onNavigate('audit-hitl')}>
          <span className="eng-tile__kicker">Findings</span>
          <span className="eng-tile__title">Review the exception queue</span>
          <span className="eng-tile__desc">Human decisions on flagged findings — signed, WORM-chained (AS 1215).</span>
          <span className="eng-tile__cta">Open →</span>
        </button>
        <Link data-testid="launch-own-data" to="/audit/new" className="eng-tile">
          <span className="eng-tile__kicker">Your data</span>
          <span className="eng-tile__title">Audit your own dataset</span>
          <span className="eng-tile__desc">Upload a CSV, map columns, get a signed certificate anyone can verify.</span>
          <span className="eng-tile__cta">Open →</span>
        </Link>
      </div>
    </section>
  );
}
