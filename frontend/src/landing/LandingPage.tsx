import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { auditApi } from '@/audit/auditApi';
import type { Scenario } from '@/audit/types';
import { useAuth } from '@/auth/AuthContext';
import { ThemeToggle } from '@/ui/ThemeToggle';
import { COMPAS } from './compasResult';
import { SealFragment } from './SealFragment';
import { useDocumentScroll } from './useDocumentScroll';
import './landing.css';

const COMPAS_ID = 'compas_recidivism';

/** What each shipped scenario audits, and whether its data is real. Keyed by
 *  scenario id so the table stays honest when the API adds or drops one. */
const SCENARIO_FACTS: Record<string, { data: string; question: string }> = {
  compas_recidivism: { data: 'Real ProPublica records', question: 'Race and two-year reoffending' },
  fair_lending: { data: 'Synthetic, known effect', question: 'A high-risk flag and loan approval' },
  healthcare_prior_auth: { data: 'Synthetic, known effect', question: 'A non-urgent flag and prior-auth approval' },
  hiring_fairness: { data: 'Synthetic, known effect', question: 'A low-fit screen and advancing' },
  insurance_underwriting: { data: 'Synthetic, known effect', question: 'A high-risk tier and claim approval' },
};

const STEPS = [
  { lead: 'Name the decision first.', line: 'Pick the outcome, the attribute it must not depend on, and what may explain it.' },
  { lead: 'Then estimate it two ways.', line: 'Double machine learning and TMLE adjust for the stated causes; four checks try to break the result.' },
  { lead: 'The verdict gets a seal.', line: 'The result is signed with ED25519 and stored at its own address, replayable byte for byte.' },
];

const METHODS: { kind: string; names: string[] }[] = [
  { kind: 'Estimators', names: ['Double machine learning', 'Targeted maximum likelihood', 'Forest doubly robust', 'Instrumental variables', 'Propensity score matching', 'Inverse propensity weighting', 'Linear regression'] },
  { kind: 'Checks', names: ['Placebo treatment', 'Random common cause', 'Data subset', 'E-value sensitivity'] },
  { kind: 'Sealing', names: ['ED25519 signatures', 'SHA-256 content address', 'Merkle ledger, RFC 6962', 'Byte-identical replay'] },
];

function StageState({ step }: { step: number }) {
  if (step === 0) {
    return (
      <dl className="stage__form">
        <dt>Outcome</dt><dd><code>two_year_recid</code></dd>
        <dt>Must not depend on</dt><dd><code>african_american</code></dd>
        <dt>May explain it</dt><dd><code>priors_count</code>, <code>age</code></dd>
        <dt>Records</dt><dd>{COMPAS.records}</dd>
      </dl>
    );
  }
  if (step === 1) {
    return (
      <table className="stage__table">
        <caption className="sr-only">Estimates in percentage points</caption>
        <thead><tr><th scope="col">Estimator</th><th scope="col">Effect</th><th scope="col">Interval</th></tr></thead>
        <tbody>
          {COMPAS.estimates.map((e) => (
            <tr key={e.method}><td>{e.method}</td><td className="num">{e.point}</td><td className="num">{e.ci}</td></tr>
          ))}
          {COMPAS.checks.map((c) => (
            <tr key={c.name}><td>{c.name}</td><td colSpan={2} className="num">{c.result}</td></tr>
          ))}
        </tbody>
      </table>
    );
  }
  return (
    <div className="stage__cert">
      <p className="stage__verdict">{COMPAS.verdict}</p>
      <p className="stage__addr"><code>/certificate/aefd338662be…a94551c0b</code></p>
      <p className="stage__signed">Signed ED25519 · replayable</p>
    </div>
  );
}

function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll<HTMLElement>('.landing [data-reveal]');
    if (typeof IntersectionObserver === 'undefined') {
      els.forEach((el) => el.classList.add('is-in'));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { e.target.classList.add('is-in'); io.unobserve(e.target); }
    }, { rootMargin: '0px 0px -10% 0px', threshold: 0.1 });
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
}

async function keyFingerprint(pem: string): Promise<string> {
  const b64 = pem.replace(/-----[^-]+-----/g, '').replace(/\s+/g, '');
  const raw = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', raw));
  const hex = Array.from(digest.slice(0, 8), (b) => b.toString(16).padStart(2, '0')).join('');
  return hex.match(/.{4}/g)!.join(' ');
}

export function LandingPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [scenarioError, setScenarioError] = useState(false);
  const [launching, setLaunching] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [rowSealed, setRowSealed] = useState(false);
  const [step, setStep] = useState(0);
  const [email, setEmail] = useState('');
  const [verifyHash, setVerifyHash] = useState('');
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const stepRefs = useRef<(HTMLDivElement | null)[]>([]);

  useDocumentScroll();
  useReveal();

  const loadScenarios = () => {
    setScenarioError(false);
    auditApi.listScenarios().then(setScenarios).catch(() => setScenarioError(true));
  };
  useEffect(loadScenarios, []);

  useEffect(() => {
    auditApi.publicKey().then((k) => keyFingerprint(k.public_key_pem)).then(setFingerprint).catch(() => setFingerprint(null));
  }, []);

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) setStep(Number((e.target as HTMLElement).dataset.step));
      }
    }, { rootMargin: '-45% 0px -45% 0px' });
    stepRefs.current.forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, []);

  const run = async (id: string) => {
    if (!user) {
      navigate('/signup', { state: { next: 'audit', scenario: id } });
      return;
    }
    setLaunching(id);
    setRunError(null);
    try {
      const { job_id } = await auditApi.runScenario(id);
      navigate(`/audit/${job_id}`);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      // BUG-255: a failed run names its own cause and keeps the list on screen.
      setRunError(/^HTTP 401/.test(message)
        ? 'Sign in to run an audit.'
        : /^HTTP 429/.test(message)
          ? 'Too many audits are running. Try again in a moment.'
          : 'The audit could not be started. Try again.');
      setLaunching(null);
    }
  };

  const startSignup = (e: React.FormEvent) => {
    e.preventDefault();
    // Router state, not a query string: an address in a URL ends up in logs.
    navigate('/signup', { state: { email } });
  };

  const verify = (e: React.FormEvent) => {
    e.preventDefault();
    const hash = verifyHash.trim().toLowerCase();
    if (/^[0-9a-f]{16,128}$/.test(hash)) navigate(`/verify/${hash}`);
  };

  return (
    <div className="landing" data-testid="landing">
      <a className="landing__skip" href="#main">Skip to content</a>

      {/* ── Hero ─────────────────────────────────────────────────── */}
      <header className="hero">
        <div className="hero__left">
          <Link to="/" className="landing__mark" aria-label="AURA home">AURA</Link>
          <div className="hero__copy">
            <h1 className="hero__title lines">
              <span className="line"><span data-line>Causal audits</span></span>{' '}
              <span className="line"><span data-line>anyone can verify</span></span>
            </h1>
            <p className="hero__lead" data-arrive>
              AURA tests whether a protected attribute caused a lending, hiring, insurance,
              healthcare or sentencing decision, then seals the answer in a signed certificate.
            </p>
            <div className="hero__actions" data-arrive>
              <button type="button" className="btn btn--accent" onClick={() => run(COMPAS_ID)} disabled={launching !== null}>
                {launching === COMPAS_ID ? 'Starting…' : 'Run the COMPAS audit'}
              </button>
              <a className="link" href="#how">How it works</a>
            </div>
            {runError && <p role="alert" data-testid="scenario-run-error" className="landing__error">{runError}</p>}
          </div>
        </div>
        <div className="hero__right">
          <nav className="hero__nav" aria-label="Account">
            <ThemeToggle />
            {user ? (
              <Link className="link" to="/workbench">Open workspace</Link>
            ) : (
              <Link className="link" to="/login">Sign in</Link>
            )}
          </nav>
          <SealFragment />
        </div>
      </header>

      <main id="main">
        {/* ── Showcase: one audit row, three states ──────────────── */}
        <section className="showcase grid" aria-labelledby="showcase-h">
          <h2 id="showcase-h" className="section-title showcase__title" data-reveal>From records to a seal</h2>
          <p className="showcase__state" data-reveal>Arrives</p>
          <div data-ui className="row row--queued" data-reveal>
            <span className="row__name">COMPAS recidivism</span>
            <span className="row__detail">{COMPAS.records} records · queued 09:41</span>
            <span className="row__badge">Queued</span>
          </div>
          <p className="showcase__state" data-reveal>Estimated</p>
          <div data-ui className={`row ${rowSealed ? 'row--sealed' : 'row--estimated'}`} data-reveal>
            <span className="row__name">COMPAS recidivism</span>
            <span className="row__detail">Adjusted effect +2.4 pts · interval −3.0 to +7.8</span>
            {rowSealed ? (
              <span className="row__badge row__badge--ok">Sealed</span>
            ) : (
              <button type="button" className="row__action" onClick={() => setRowSealed(true)}>Seal result</button>
            )}
          </div>
          <p className="showcase__state" data-reveal>Sealed</p>
          <div data-ui className="row row--sealed" data-reveal>
            <span className="row__name">COMPAS recidivism</span>
            <span className="row__detail">Signed ED25519 · <code>aefd3386…a94551c0b</code></span>
            <span className="row__badge row__badge--ok">Verified</span>
          </div>
        </section>

        {/* ── How it works: pinned fragment ──────────────────────── */}
        <section id="how" className="how grid" aria-labelledby="how-h">
          <div className="how__steps">
            <h2 id="how-h" className="section-title">How an audit runs</h2>
            {STEPS.map((s, i) => (
              <div
                key={s.lead}
                ref={(el) => { stepRefs.current[i] = el; }}
                data-step={i}
                className={`how__step ${step === i ? 'is-active' : ''}`}
              >
                <p className="how__lead">{s.lead}</p>
                <p className="how__line">{s.line}</p>
                {/* Phones: no pinned stage over the text; each statement shows its state. */}
                <div data-ui className="stage how__inline" aria-hidden="true"><StageState step={i} /></div>
              </div>
            ))}
          </div>
          <div className="how__stage">
            <div data-ui className="stage" aria-live="polite">
              <StageState step={step} />
            </div>
          </div>
        </section>

        <div className="band" aria-label="Where these decisions are made">
          <figure className="band__main" data-reveal>
            <img
              src="/landing/courtroom-u-s-courthouse-and-post-office.jpg"
              width={1920}
              height={1304}
              alt="A wood-panelled federal courtroom with the bench under a painted mural"
              loading="lazy"
              decoding="async"
            />
            <figcaption>Sentencing. Courtroom, U.S. Courthouse, Pittsburgh.</figcaption>
          </figure>
          <figure className="band__side" data-reveal>
            <img
              src="/landing/arbacoochee-fire-insurance-map.jpg"
              width={1353}
              height={799}
              alt="A hand-drawn 1922 fire insurance map of Main Street, Arbacoochee, marking each building's risk"
              loading="lazy"
              decoding="async"
            />
            <figcaption>Underwriting, by hand. Fire insurance map, Arbacoochee, Alabama, 1922.</figcaption>
          </figure>
        </div>

        {/* ── Proof: methods you can look up ─────────────────────── */}
        <section className="proof grid" aria-labelledby="proof-h">
          <h2 id="proof-h" className="section-title proof__title" data-reveal>Methods you can look up</h2>
          <div className="proof__cols" data-reveal>
            {METHODS.map((m) => (
              <ul key={m.kind} className="proof__col">
                <li className="proof__kind">{m.kind}</li>
                {m.names.map((n) => <li key={n}>{n}</li>)}
              </ul>
            ))}
          </div>
        </section>

        {/* ── List: the five scenarios ───────────────────────────── */}
        <section className="scenarios grid" aria-labelledby="scen-h">
          <h2 id="scen-h" className="scenarios__title" data-reveal>
            <span className="scenarios__indent">Five decisions,</span> ready to audit
          </h2>
          <figure className="scenarios__photo" data-reveal>
            <img
              src="/landing/ft-lauderdale-fl-courthouse-broward-county-11.jpg"
              width={1920}
              height={1280}
              alt="The Broward County Judicial Complex in Fort Lauderdale, behind royal palms"
              loading="lazy"
              decoding="async"
            />
            <figcaption>Broward County, Florida, where the COMPAS records were collected.</figcaption>
          </figure>
          <div className="scenarios__table" data-reveal>
            {scenarioError && (
              <p className="landing__error" data-testid="scenarios-error">
                The scenario list did not load. <button type="button" className="link" onClick={loadScenarios}>Try again</button>
              </p>
            )}
            {!scenarios && !scenarioError && <p className="scenarios__loading" data-testid="scenarios-loading">Loading the scenarios…</p>}
            {scenarios && (
              <table>
                <caption className="sr-only">Audit scenarios</caption>
                <thead>
                  <tr><th scope="col">Area</th><th scope="col">Question</th><th scope="col">Data</th><th scope="col"><span className="sr-only">Run</span></th></tr>
                </thead>
                <tbody>
                  {scenarios.map((s) => {
                    const facts = SCENARIO_FACTS[s.id];
                    return (
                      <tr key={s.id} data-testid={`scenario-row-${s.id}`}>
                        <td className="scenarios__kind">{s.vertical.charAt(0).toUpperCase() + s.vertical.slice(1)}</td>
                        <td>{facts?.question ?? s.title}</td>
                        <td className="scenarios__data">{facts?.data ?? ''}</td>
                        <td className="scenarios__run">
                          <button type="button" className="link" onClick={() => run(s.id)} disabled={launching !== null}
                            aria-label={`Run the ${s.title} audit`}>
                            {launching === s.id ? 'Starting…' : 'Run →'}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            <p className="scenarios__more">
              Have your own data? <Link className="link" to="/audit/new">Audit an uploaded file</Link>
            </p>
          </div>
        </section>

        {/* ── CTA: a sentence with a blank ───────────────────────── */}
        <section className="cta" aria-labelledby="cta-h">
          <h2 id="cta-h" className="section-title">Start auditing</h2>
          <form className="cta__form" onSubmit={startSignup}>
            <p className="cta__sentence">
              Audit your first decision as{' '}
              <label className="sr-only" htmlFor="cta-email">Work email</label>
              <input
                id="cta-email"
                type="email"
                required
                autoComplete="email"
                placeholder="you@work.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />{' '}
              <button type="submit" className="cta__go">today</button>
            </p>
            <p className="cta__note">Creates your workspace. The first certificate takes about a minute.</p>
          </form>
          <div data-ui className="cta__receipt" aria-label="What you receive">
            <p className="cta__receipt-title">Certificate</p>
            <dl>
              <dt>Decision</dt><dd>{email ? `Audited by ${email}` : 'Yours'}</dd>
              <dt>Verdict</dt><dd>Stated in one sentence</dd>
              <dt>Signature</dt><dd>ED25519, checkable by anyone</dd>
              <dt>Address</dt><dd><code>/certificate/&lt;its hash&gt;</code></dd>
            </dl>
          </div>
        </section>
      </main>

      {/* ── Footer of tiles ──────────────────────────────────────── */}
      <footer className="tiles">
        <div className="tile tile--statement">
          <p>Every verdict AURA issues carries its own signature.</p>
          <p className="tile__small">© 2026 AURA</p>
        </div>
        <nav className="tile tile--nav" aria-label="Footer">
          <ul>
            <li><button type="button" className="link" onClick={() => run(COMPAS_ID)}>Run the COMPAS audit</button></li>
            <li><Link className="link" to="/audit/new">Audit your own data</Link></li>
            <li><Link className="link" to="/login">Sign in</Link></li>
            <li><Link className="link" to="/signup">Create an account</Link></li>
          </ul>
        </nav>
        <div className="tile tile--key">
          <p>Signing key</p>
          <p className="tile__key" data-testid="key-fingerprint">{fingerprint ? <code>{fingerprint}</code> : 'Unavailable right now'}</p>
          <p className="tile__small">SHA-256 of the live ED25519 public key</p>
        </div>
        <form className="tile tile--verify" onSubmit={verify}>
          <label htmlFor="verify-hash">Verify a certificate</label>
          <input id="verify-hash" value={verifyHash} onChange={(e) => setVerifyHash(e.target.value)} placeholder="Paste its hash" spellCheck={false} />
          <button type="submit" className="link">Check →</button>
        </form>
        <div className="tile tile--word" aria-hidden="true">AURA</div>
        <button type="button" className="tile tile--accent" onClick={() => run(COMPAS_ID)}>
          <span>Start with COMPAS</span>
          <svg viewBox="0 0 48 48" width="48" height="48" aria-hidden="true"><path d="M10 38 38 10M16 10h22v22" fill="none" stroke="currentColor" strokeWidth="4" /></svg>
        </button>
        <p className="tile tile--credits"><span className="tile__credits-text">
          Photographs: “Courtroom, U.S. Courthouse and Post Office, Pittsburgh” by Carol M. Highsmith, public domain;
          “Arbacoochee fire insurance map” by the Alabama Inspection and Rating Bureau, public domain;
          “Ft. Lauderdale, FL, Courthouse, Broward County” by Georgia Guercio,{' '}
          <a className="link" href="https://creativecommons.org/licenses/by-sa/3.0/" rel="license noreferrer" target="_blank">CC BY-SA 3.0</a>, via Wikimedia Commons.
        </span></p>
      </footer>
    </div>
  );
}
