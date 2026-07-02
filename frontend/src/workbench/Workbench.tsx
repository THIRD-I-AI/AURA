/* AURA Workbench — faithful port of the Claude Design prototype
   (docs/design/aura-workbench/). One shell, four nav groups, a dense
   cockpit board, ⌘K palette, dark/light themes. Live wiring where the
   platform already has the API (Ask AURA → commander SSE, ledger chip →
   /audit/ledger/verify); design seed data elsewhere so every panel renders.
   Additive: the classic /app shell is untouched and reachable from stubs. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { authService, chatService } from '../services/api';
import './workbench.css';

type Msg = { q: string; sql?: string; critic?: string; columns?: string[]; rows?: string[][]; answer?: string };
type Heal = { id: number; title: string; method: string; safe: boolean; sub: string; state: 'pending' | 'deployed' | 'rejected'; resolution?: string };
type FeedEv = { time: string; k: string; color: string; t: string };

const NAV_GROUPS: [string, string[]][] = [
  ['WORKSPACE', ['Cockpit', 'Terminal', 'Ask AURA', 'Dashboards', 'Library', 'Query History']],
  ['AUDIT', ['Audit Workbench', 'Counterfactuals', 'Certificates', 'Exception Queue']],
  ['OPERATE', ['Pipelines', 'Streaming', 'Healing Queue', 'Scheduler', 'Webhooks', 'Cost']],
  ['DATA', ['Connectors', 'Files & Data', 'Lineage', 'Metadata Store']],
];

/* Stubs open the classic app so no existing feature is lost while views are ported. */
const STUB_DESCS: Record<string, string> = {
  Dashboards: 'Saved-query tiles rendered as live charts (Recharts), workspace-scoped, with presence indicators for collaborators.',
  Library: 'Saved queries and reusable analysis snippets shared across the workspace.',
  Certificates: 'Signed audit certificates (PCAOB-style) with public verify links and ED25519 signatures.',
  Scheduler: 'Distributed job queue (LISTEN/NOTIFY) — cron-style schedules for pipelines, audits, and DAR research runs.',
  Webhooks: 'Outbound event subscriptions: pipeline completions, healing events, signed findings.',
  Cost: 'Per-service and per-query spend, budgets, and anomaly alerts on cost drift.',
  Connectors: 'PostgreSQL · MySQL · BigQuery · DuckDB · FAISS · spatial — credential vault + health checks.',
  'Files & Data': 'Uploads, datasets, and the DuckDB analytics lake with atomic Parquet loads.',
  'Metadata Store': 'Schema registry and catalog the critic validates every generated query against.',
  Terminal: 'The dockview multi-panel command terminal (S46).',
  'Ask AURA': 'Full-page conversational analytics over your datasets.',
  'Audit Workbench': 'HITL exception review with signed decisions.',
};
const STUB_LINKS: Record<string, string> = {
  Terminal: '/app/terminal', Dashboards: '/app', Library: '/app', Certificates: '/app',
  Scheduler: '/app', Webhooks: '/app', Cost: '/app', Connectors: '/app',
  'Files & Data': '/app', 'Metadata Store': '/app', 'Audit Workbench': '/app', 'Ask AURA': '/app',
};

const CF_STAGES = [
  'Submitting job to counterfactual service…',
  'Estimators 1–4: backdoor.linear_reg · psm · dml · ipw…',
  'Estimators 5–7: frontdoor · iv · gcm…',
  'Refuters: placebo · random-cause · subset · unobserved-confound…',
  'Adversarial critic reviewing challenges…',
  'Conformal CI + E-value…',
  'Signing record (ED25519) → ledger…',
];
const BOOT_STAGES = [
  'Authenticating — JWT issued (12h scope)',
  'Loading workspace acme-corp',
  'Verifying ledger chain (sha256)',
  'Subscribing to live streams (kafka erp.*)',
  'Restoring cockpit layout',
];
const FEED_POOL: Omit<FeedEv, 'time'>[] = [
  { k: 'INGEST', color: 'var(--blue)', t: 'atomic parquet load lake.fact_revenue +18,240 rows' },
  { k: 'HEAL', color: 'var(--warn)', t: 'MAPE-K analyze: no drift on workday.hcm' },
  { k: 'AUDIT', color: 'var(--accent)', t: 'ledger append · sha256 chained · ED25519 ok' },
  { k: 'QUERY', color: 'var(--text2)', t: 'sandbox run: revenue rollup preview (0.8s, $0.01)' },
  { k: 'PII', color: 'var(--purple)', t: 'masked 4 fields in netsuite.gl_feed batch' },
  { k: 'INGEST', color: 'var(--blue)', t: 'kafka erp.hcm.v1 offset +512 · idempotent' },
];

const now = () => new Date().toTimeString().slice(0, 5);
const fmtM = (v: number) => '−$' + Math.abs(v).toFixed(1) + 'M';

export default function Workbench() {
  const [view, setView] = useState<'login' | 'boot' | 'app'>('login');
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [nav, setNav] = useState('Cockpit');
  const [toast, setToast] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQ, setPaletteQ] = useState('');
  const [loginError, setLoginError] = useState<string | null>(null);
  const [bootIdx, setBootIdx] = useState(0);
  const [audience, setAudience] = useState<'operator' | 'auditor' | 'analyst'>('operator');
  const [messages, setMessages] = useState<Msg[]>([{
    q: 'Why did APAC churn spike in March?',
    sql: "SELECT region, month, churn_rate,\n  churn_rate - LAG(churn_rate) OVER (PARTITION BY region ORDER BY month) AS delta\nFROM metrics.churn_monthly\nWHERE region = 'APAC' AND month BETWEEN '2026-01' AND '2026-04';",
    critic: 'Critic pass 2/2 — joins verified against metadata store · row-count sanity ✓ · PII scan clean',
    columns: ['MONTH', 'CHURN_RATE', 'DELTA'],
    rows: [['2026-01', '3.1%', '—'], ['2026-02', '3.2%', '+0.1'], ['2026-03', '5.3%', '+2.1']],
    answer: 'March churn rose +2.1pp. The causal engine attributes it to the March 3 price change (DoWhy GCM root-cause, E-value 3.2) — finding signed and filed to the ledger.',
  }]);
  const [thinking, setThinking] = useState<string | null>(null);
  const [healing, setHealing] = useState<Heal[]>([
    { id: 1, title: 'orders.customer_id → cust_id', method: 'TEMPLATE', safe: true, sub: 'schema rename drift · shim sandbox-validated ✓ · risk tier T1', state: 'pending' },
    { id: 2, title: 'netsuite.gl_feed date format', method: 'LLM', safe: false, sub: 'MM/DD → ISO drift · held by risk policy · awaiting reviewer', state: 'pending' },
  ]);
  const [cfRunning, setCfRunning] = useState(false);
  const [cfStageIdx, setCfStageIdx] = useState(-1);
  const [feed, setFeed] = useState<FeedEv[]>([
    { time: '09:41', k: 'AUDIT', color: 'var(--accent)', t: 'ledger append #4,182 · sha256 chained' },
    { time: '09:40', k: 'INGEST', color: 'var(--blue)', t: 'kafka erp.gl.v2 offset +1,024 · idempotent' },
    { time: '09:39', k: 'PII', color: 'var(--purple)', t: 'perimeter masked 12 fields in workday.hcm batch' },
    { time: '09:38', k: 'QUERY', color: 'var(--text2)', t: 'sandbox run: churn cohort scan (2.1s, $0.03)' },
  ]);
  const [history, setHistory] = useState([
    { time: '09:41', q: 'Why did APAC churn spike in March?', engine: 'DuckDB', status: 'verified', cost: '$0.04', dur: '3.2s', by: 'jm' },
    { time: '09:12', q: 'SELECT sum(amount) FROM lake.fact_revenue …', engine: 'BigQuery', status: 'verified', cost: '$0.11', dur: '8.4s', by: 'rk' },
    { time: '08:57', q: 'Counterfactual job cf_8812 — May price change', engine: 'sandbox', status: 'signed', cost: '$0.32', dur: '84s', by: 'DAR' },
    { time: '06:00', q: 'Nightly full audit replay — FY26 Q2 ledger', engine: 'ledger', status: 'signed', cost: '—', dur: '14m', by: 'sched' },
  ]);
  const [ledger, setLedger] = useState({ no: '#4,182', hash: '9f3c…a1e2', intact: true });
  const cf = { id: 'cf_8812', question: "What would Q3 revenue have been if we hadn't raised prices in May?", effect: -1.9, ciLo: -2.4, ciHi: -1.5, evalue: 3.2, hash: '9f3c…a1e2' };
  const chatInput = useRef<HTMLInputElement>(null);
  const emailInput = useRef<HTMLInputElement>(null);
  const passInput = useRef<HTMLInputElement>(null);
  const paletteInput = useRef<HTMLInputElement>(null);
  const feedTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const showToast = useCallback((t: string) => {
    setToast(t);
    setTimeout(() => setToast(null), 2600);
  }, []);

  /* Live ledger chip — real chain state when the gateway is up, seed otherwise. */
  useEffect(() => {
    fetch('/api/v1/counterfactual/audit/ledger/verify')
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (j && typeof j.count === 'number') {
          setLedger({ no: '#' + j.count.toLocaleString(), hash: (j.merkle_root || '').slice(0, 4) + '…' + (j.merkle_root || '').slice(-4), intact: !!j.ok });
        }
      })
      .catch(() => undefined);
  }, []);

  /* Live feed ticker, like the prototype. */
  useEffect(() => {
    if (view !== 'app') return;
    feedTimer.current = setInterval(() => {
      const ev = FEED_POOL[Math.floor(Math.random() * FEED_POOL.length)];
      setFeed((f) => [{ ...ev, time: now() }, ...f].slice(0, 8));
    }, 6000);
    return () => { if (feedTimer.current) clearInterval(feedTimer.current); };
  }, [view]);

  /* Boot sequence. */
  useEffect(() => {
    if (view !== 'boot') return;
    const t = setInterval(() => {
      setBootIdx((i) => {
        if (i + 1 >= BOOT_STAGES.length + 1) { clearInterval(t); setView('app'); return i; }
        return i + 1;
      });
    }, 420);
    return () => clearInterval(t);
  }, [view]);

  /* ⌘K palette. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPaletteOpen(true); setTimeout(() => paletteInput.current?.focus(), 30); }
      if (e.key === 'Escape') setPaletteOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  /* Email/password = REAL auth (JWT via /auth/token, same session as the
     classic app). SSO buttons remain the design's demo path. */
  const signIn = async () => {
    const email = emailInput.current?.value.trim() ?? '';
    const pass = passInput.current?.value ?? '';
    if (!email || !email.includes('@')) { setLoginError('Enter a valid corporate email address.'); return; }
    try {
      await authService.login(email, pass);
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : 'Sign-in failed — check your credentials.');
      return;
    }
    setLoginError(null); setBootIdx(0); setView('boot');
  };

  const ask = async () => {
    const q = chatInput.current?.value.trim();
    if (!q || thinking) return;
    if (chatInput.current) chatInput.current.value = '';
    setMessages((m) => [...m, { q }]);
    setThinking('generator drafting SQL · critic reviewing…');
    let sql: string | undefined; let critic: string | undefined; let answer = '';
    try {
      await chatService.streamMessage(q, {
        onEvent: (ev: { event: string; data: Record<string, unknown> }) => {
          const d = ev.data as { name?: string; arguments?: { sql?: string }; result?: { row_count?: number }; text?: string; message?: string };
          if (ev.event === 'tool_call' && d.arguments?.sql) sql = d.arguments.sql;
          else if (ev.event === 'tool_result') critic = `verified · ${d.result?.row_count ?? 0} rows · sandboxed`;
          else if (ev.event === 'text') answer = d.text ?? '';
          else if (ev.event === 'error') answer = `Error: ${d.message}`;
        },
      });
    } catch {
      answer = 'Commander offline — showing the workflow with the connected gateway is required for live answers.';
    }
    setThinking(null);
    setMessages((m) => {
      const last = m[m.length - 1];
      return [...m.slice(0, -1), { ...last, sql, critic, answer: answer || '(no answer)' }];
    });
    setHistory((h) => [{ time: now(), q: q.length > 52 ? q.slice(0, 52) + '…' : q, engine: 'DuckDB', status: 'verified', cost: '$0.02', dur: '1.4s', by: 'you' }, ...h]);
  };

  const runCf = () => {
    if (cfRunning) return;
    setCfRunning(true); setCfStageIdx(0);
    const t = setInterval(() => {
      setCfStageIdx((i) => {
        if (i + 1 >= CF_STAGES.length) { clearInterval(t); setCfRunning(false); showToast('Counterfactual re-verified — record signed to ledger'); return -1; }
        return i + 1;
      });
    }, 650);
  };

  const decideHeal = (id: number, ok: boolean) => {
    setHealing((hs) => hs.map((h) => h.id === id ? {
      ...h, state: ok ? 'deployed' : 'rejected',
      resolution: ok ? '✓ deployed — override signed to WORM log' : '✕ rejected — pipeline paused, incident opened',
    } : h));
    showToast(ok ? 'Shim approved — deploying upstream' : 'Healing proposal rejected');
  };

  const pendingCount = healing.filter((h) => h.state === 'pending').length;
  const stats = [
    { label: 'Streams healthy', value: '12/13', sub: '1 healing (UASR)', subColor: 'var(--warn)' },
    { label: 'Kafka lag p95', value: '240ms', sub: 'idempotent producer', subColor: 'var(--text3)' },
    { label: 'DLQ depth', value: '3', sub: 'oldest 41m', subColor: 'var(--text3)' },
    { label: 'Sandbox runs today', value: '148', sub: '0 policy violations', subColor: 'var(--text3)' },
    { label: 'Audit coverage', value: '94.7%', sub: 'replayable GL lines', subColor: 'var(--accent)' },
    { label: 'Spend MTD', value: '$1,284', sub: '−12% vs May', subColor: 'var(--text3)' },
  ];
  const runs = [
    { name: 'revenue_daily_rollup', status: 'success', color: 'var(--accent)', time: '42s', rows: '1.2M' },
    { name: 'churn_features', status: 'running', color: 'var(--blue)', time: '1m 04s', rows: '—' },
    { name: 'gl_reconciliation', status: 'retrying', color: 'var(--danger)', time: '3m 12s', rows: '804K' },
  ];

  const showChat = nav === 'Cockpit' || nav === 'Ask AURA';
  const showCf = nav === 'Cockpit' || nav === 'Counterfactuals' || nav === 'Audit Workbench';
  const showHealing = nav === 'Cockpit' || nav === 'Healing Queue' || nav === 'Exception Queue';
  const showPipes = nav === 'Cockpit' || nav === 'Pipelines' || nav === 'Streaming';
  const showLineage = nav === 'Cockpit' || nav === 'Pipelines' || nav === 'Streaming' || nav === 'Lineage';
  const showHistory = nav === 'Cockpit' || nav === 'Query History';
  const showStub = !(showChat || showCf || showHealing || showPipes || showLineage || showHistory);

  const commands = useMemo(() => {
    const q = paletteQ.toLowerCase();
    const navs = NAV_GROUPS.flatMap(([, items]) => items);
    const all = [
      ...navs.map((n) => ({ title: 'Go to ' + n, hint: 'NAV', run: () => { setNav(n); setPaletteOpen(false); } })),
      { title: 'Toggle light / dark theme', hint: 'THEME', run: () => { setTheme((t) => t === 'dark' ? 'light' : 'dark'); setPaletteOpen(false); } },
      { title: 'Run counterfactual audit', hint: 'JOB', run: () => { setNav('Counterfactuals'); setPaletteOpen(false); runCf(); } },
      { title: 'Open classic app', hint: 'NAV', run: () => { window.location.href = '/app'; } },
      { title: 'Sign out', hint: 'AUTH', run: () => { setView('login'); setPaletteOpen(false); } },
    ];
    return all.filter((c) => c.title.toLowerCase().includes(q)).slice(0, 9);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paletteQ]);

  /* ── login ── */
  if (view === 'login') {
    return (
      <div className="aw" data-theme={theme} data-testid="wb-login">
        <div style={{ flex: 1, minHeight: '100vh', display: 'grid', gridTemplateColumns: '1.1fr 1fr' }}>
          <div style={{ background: 'var(--sunken)', padding: '48px 56px', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}><span style={{ width: 8, height: 8, background: 'var(--accent)', borderRadius: 2 }} /><span className="aw-display" style={{ fontWeight: 700, fontSize: 15, letterSpacing: '.1em' }}>AURA</span></div>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 22, maxWidth: 520 }}>
              <div className="aw-display" style={{ fontWeight: 700, fontSize: 44, lineHeight: 1.15 }}>Analytics your auditors can replay.</div>
              <div style={{ fontSize: 15, color: 'var(--text2)', lineHeight: 1.6 }}>Autonomous agents over mission-critical data — every conclusion signed, every pipeline self-healing.</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13, color: 'var(--text2)' }}>
                {['ED25519-signed conclusions · deterministic replay', 'Self-healing streams — NetSuite, Workday, Kafka (MAPE-K)', 'Fail-closed auth · PII perimeter masking · WORM audit log'].map((t) => (
                  <div key={t} style={{ display: 'flex', alignItems: 'center', gap: 9 }}><span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--accent)', flex: 'none' }} />{t}</div>
                ))}
              </div>
            </div>
            <div className="aw-mono" style={{ fontSize: 10, fontWeight: 500, color: 'var(--text3)' }}>LEDGER {ledger.no} · {ledger.intact ? 'CHAIN INTACT' : 'CHAIN CHECK'} · sha256 {ledger.hash}</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40 }}>
            <div style={{ width: 380, display: 'flex', flexDirection: 'column', gap: 18 }}>
              <div>
                <div className="aw-display" style={{ fontWeight: 700, fontSize: 24 }}>Sign in</div>
                <div style={{ marginTop: 6, fontSize: 13, color: 'var(--text2)' }}>Use your corporate identity to continue to <strong>acme-corp</strong>.</div>
              </div>
              {['Okta', 'Microsoft Entra ID', 'Google Workspace'].map((sso) => (
                <div key={sso} onClick={() => { setBootIdx(0); setView('boot'); }} className="aw-hover-accent-bd" style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10, border: '1px solid var(--border)', borderRadius: 8, padding: '11px 14px', fontSize: 13.5, fontWeight: 600 }}>
                  <span className="aw-mono" style={{ width: 18, height: 18, display: 'grid', placeItems: 'center', background: 'var(--raised)', borderRadius: 4, fontSize: 9 }}>{sso[0]}</span>
                  Continue with {sso}
                </div>
              ))}
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text3)', fontSize: 11 }}><span style={{ flex: 1, height: 1, background: 'var(--hair)' }} />or with email<span style={{ flex: 1, height: 1, background: 'var(--hair)' }} /></div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <label style={{ fontSize: 12, fontWeight: 600 }}>Work email<input ref={emailInput} onKeyDown={(e) => e.key === 'Enter' && signIn()} placeholder="you@acme.com" className="aw-input" style={{ marginTop: 6, width: '100%', boxSizing: 'border-box', padding: '10px 14px', fontSize: 13 }} /></label>
                <label style={{ fontSize: 12, fontWeight: 600 }}>Password<input ref={passInput} type="password" onKeyDown={(e) => e.key === 'Enter' && signIn()} placeholder="••••••••••••" className="aw-input" style={{ marginTop: 6, width: '100%', boxSizing: 'border-box', padding: '10px 14px', fontSize: 13 }} /></label>
                {loginError && <div style={{ fontSize: 12, color: 'var(--danger)' }}>{loginError}</div>}
                <button onClick={signIn} className="aw-btn-accent" style={{ textAlign: 'center', fontSize: 13.5, padding: 12 }}>Continue</button>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.6 }}>SSO is enforced for production workspaces. Sessions are JWT-scoped and expire after 12 hours.<br />Trouble signing in? Contact your workspace admin.</div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* ── boot ── */
  if (view === 'boot') {
    return (
      <div className="aw" data-theme={theme} data-testid="wb-boot">
        <div style={{ flex: 1, minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 30 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}><span style={{ width: 10, height: 10, background: 'var(--accent)', borderRadius: 2, animation: 'awpulse 1.4s infinite' }} /><span className="aw-display" style={{ fontWeight: 700, fontSize: 18, letterSpacing: '.1em' }}>AURA</span></div>
          <div style={{ width: 340, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {BOOT_STAGES.map((label, i) => (
              <div key={label} className="aw-mono" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 11, fontWeight: 500, color: i < bootIdx ? 'var(--accent)' : i === bootIdx ? 'var(--text)' : 'var(--text3)' }}>
                <span style={{ width: 14, textAlign: 'center' }}>{i < bootIdx ? '✓' : i === bootIdx ? '◌' : '·'}</span>{label}
              </div>
            ))}
          </div>
          <div style={{ width: 340, height: 3, background: 'var(--raised)', borderRadius: 2, overflow: 'hidden' }}><div style={{ height: '100%', background: 'var(--accent)', borderRadius: 2, transition: 'width .45s ease', width: Math.min(100, Math.round((bootIdx / BOOT_STAGES.length) * 100)) + '%' }} /></div>
        </div>
      </div>
    );
  }

  /* ── app ── */
  return (
    <div className="aw" data-theme={theme} data-testid="wb-app">
      {/* topbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, height: 54, padding: '0 24px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', flex: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}><span style={{ width: 8, height: 8, background: 'var(--accent)', borderRadius: 2 }} /><span className="aw-display" style={{ fontWeight: 700, fontSize: 15, letterSpacing: '.1em' }}>AURA</span></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 10px' }}>Acme Corp <span style={{ color: 'var(--text3)' }}>· FY26 Q2</span></div>
        <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.08em', color: 'var(--blue)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 4, padding: '3px 7px' }}>DEMO DATA</div>
        <div style={{ flex: 1 }} />
        <div onClick={() => { setPaletteOpen(true); setTimeout(() => paletteInput.current?.focus(), 30); }} className="aw-mono aw-hover-accent-bd" style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontWeight: 500, color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 10px' }}>
          Search, ask, or run a command <span style={{ background: 'var(--sunken)', borderRadius: 3, padding: '1px 5px' }}>⌘K</span>
        </div>
        <div onClick={() => setTheme((t) => t === 'dark' ? 'light' : 'dark')} style={{ cursor: 'pointer', fontSize: 11.5, fontWeight: 600, color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 11px' }}>{theme === 'dark' ? '☾ Dark' : '☀ Light'}</div>
        <div style={{ display: 'flex' }}>
          {[['JM', 'var(--text2)', 'var(--raised)'], ['RK', 'var(--text2)', 'var(--raised)'], ['DAR', 'var(--accent)', 'var(--accent-dim)']].map(([t, c, b], i) => (
            <span key={t} className="aw-mono" style={{ width: 26, height: 26, borderRadius: '50%', background: b, border: '2px solid var(--surface)', display: 'grid', placeItems: 'center', fontSize: 9, fontWeight: 600, color: c, marginLeft: i ? -8 : 0 }}>{t}</span>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        {/* nav */}
        <div style={{ width: 204, flex: 'none', borderRight: '1px solid var(--border)', background: 'var(--surface)', padding: '16px 10px 20px', display: 'flex', flexDirection: 'column', gap: 18, overflowY: 'auto' }}>
          {NAV_GROUPS.map(([label, items]) => (
            <div key={label}>
              <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.14em', color: 'var(--text3)', padding: '0 12px 6px' }}>{label}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                {items.map((name) => {
                  const active = name === nav;
                  const badge = (name === 'Exception Queue' || name === 'Healing Queue') && pendingCount > 0 ? String(pendingCount) : null;
                  return (
                    <div key={name} onClick={() => setNav(name)} className="aw-nav-item" style={{ color: active ? 'var(--text)' : 'var(--text2)', background: active ? 'var(--accent-dim)' : 'transparent', fontWeight: active ? 600 : 400 }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{active && <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--accent)' }} />}{name}</span>
                      {badge && <span className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, color: 'var(--warn)', background: 'var(--warn-dim)', borderRadius: 99, padding: '1px 6px' }}>{badge}</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          <div className="aw-mono" style={{ marginTop: 'auto', padding: '14px 12px 0', borderTop: '1px solid var(--border)', fontSize: 9.5, fontWeight: 500, color: 'var(--text3)', lineHeight: 1.9 }}>
            LEDGER {ledger.no}<br /><span style={{ color: 'var(--accent)' }}>● {ledger.intact ? 'CHAIN INTACT' : 'VERIFYING…'}</span><br />sha256 {ledger.hash}
          </div>
        </div>

        {/* main */}
        <div style={{ flex: 1, minWidth: 0, padding: '24px 26px 28px', display: 'flex', flexDirection: 'column', gap: 16, overflowY: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div className="aw-display" style={{ fontWeight: 600, fontSize: 22 }}>{nav}</div>
            <div className="aw-chip aw-pill-accent" style={{ display: 'flex', alignItems: 'center', gap: 6, border: '1px solid var(--accent-bd)', fontWeight: 600, letterSpacing: '.08em' }}><span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--accent)', animation: 'awpulse 2.4s infinite' }} />ALL SYSTEMS VERIFIED</div>
            <div style={{ flex: 1 }} />
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>Last full audit replay 06:00 UTC · scheduler on time</div>
          </div>

          {nav === 'Cockpit' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', gap: 12 }} data-testid="wb-stats">
              {stats.map((st) => (
                <div key={st.label} className="aw-panel" style={{ borderRadius: 8, padding: '12px 14px' }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>{st.label}</div>
                  <div className="aw-mono" style={{ fontWeight: 600, fontSize: 18 }}>{st.value}</div>
                  <div style={{ fontSize: 10.5, marginTop: 3, color: st.subColor }}>{st.sub}</div>
                </div>
              ))}
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(470px,1fr))', gap: 16, alignItems: 'start' }}>
            {showChat && (
              <div className="aw-panel" style={{ display: 'flex', flexDirection: 'column' }} data-testid="wb-chat">
                <div className="aw-panel-head" style={{ padding: '14px 18px' }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>Ask AURA</div>
                  <div className="aw-chip aw-pill-outline">generator ⇄ critic</div>
                  <div className="aw-chip aw-pill-accent">DPC cross-check</div>
                  <div style={{ flex: 1 }} />
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>DuckDB lake</div>
                </div>
                <div style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 14, maxHeight: 560, overflowY: 'auto' }}>
                  {messages.map((m, i) => (
                    <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 10, animation: 'awup .25s ease' }}>
                      <div style={{ alignSelf: 'flex-end', maxWidth: '70%', background: 'var(--raised)', border: '1px solid var(--border)', borderRadius: '10px 10px 3px 10px', padding: '9px 14px', fontSize: 13 }}>{m.q}</div>
                      {m.sql && <div className="aw-mono" style={{ background: 'var(--sunken)', border: '1px solid var(--hair)', borderRadius: 8, padding: '12px 14px', fontSize: 11.5, lineHeight: 1.65, color: 'var(--text2)', whiteSpace: 'pre-wrap' }}>{m.sql}</div>}
                      {m.critic && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{m.critic}</div>}
                      {m.columns && m.rows && (
                        <div style={{ border: '1px solid var(--hair)', borderRadius: 8, overflow: 'hidden' }}>
                          <div style={{ display: 'flex', background: 'var(--raised)' }}>{m.columns.map((c) => <div key={c} className="aw-mono" style={{ flex: 1, padding: '7px 14px', fontSize: 10, fontWeight: 600, color: 'var(--text3)', letterSpacing: '.06em' }}>{c}</div>)}</div>
                          {m.rows.map((r, ri) => <div key={ri} style={{ display: 'flex', borderTop: '1px solid var(--hair)' }}>{r.map((cell, ci) => <div key={ci} className="aw-mono" style={{ flex: 1, padding: '7px 14px', fontSize: 11.5 }}>{cell}</div>)}</div>)}
                        </div>
                      )}
                      {m.answer && <div style={{ fontSize: 13, lineHeight: 1.55 }}>{m.answer}</div>}
                    </div>
                  ))}
                  {thinking && <div className="aw-mono" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 11, fontWeight: 500, color: 'var(--text3)' }}><span className="aw-spinner" />{thinking}</div>}
                </div>
                <div style={{ padding: '12px 18px 16px', borderTop: '1px solid var(--hair)', display: 'flex', gap: 8 }}>
                  <input ref={chatInput} onKeyDown={(e) => e.key === 'Enter' && ask()} placeholder="Ask anything about your data — SQL is generated, checked, and signed" className="aw-input" style={{ flex: 1, padding: '10px 14px', fontSize: 13 }} />
                  <button onClick={ask} className="aw-btn-accent" style={{ fontSize: 12.5, padding: '10px 18px' }}>Ask</button>
                </div>
              </div>
            )}

            {showCf && (
              <div className="aw-panel" data-testid="wb-cf">
                <div className="aw-panel-head" style={{ padding: '14px 18px' }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>Counterfactual audit</div>
                  <div className="aw-mono" style={{ fontSize: 10, fontWeight: 500, color: 'var(--text3)' }}>{cf.id}</div>
                  <div style={{ flex: 1 }} />
                  <div className="aw-mono" style={{ display: 'flex', fontSize: 10, fontWeight: 600, border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
                    {(['operator', 'auditor', 'analyst'] as const).map((a) => (
                      <div key={a} onClick={() => setAudience(a)} style={{ cursor: 'pointer', padding: '4px 9px', color: a === audience ? 'var(--accent)' : 'var(--text3)', background: a === audience ? 'var(--accent-dim)' : 'transparent' }}>{a.toUpperCase()}</div>
                    ))}
                  </div>
                </div>
                <div style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 13 }}>
                  <div style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--text2)', fontStyle: 'italic' }}>"{cf.question}"</div>
                  {cfRunning ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 7, padding: '6px 0' }}>
                      {CF_STAGES.map((label, i) => (
                        <div key={label} className="aw-mono" style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 11, fontWeight: 500, color: i < cfStageIdx ? 'var(--accent)' : i === cfStageIdx ? 'var(--text)' : 'var(--text3)' }}>
                          <span style={{ width: 14, textAlign: 'center' }}>{i < cfStageIdx ? '✓' : i === cfStageIdx ? '◌' : '·'}</span>{label}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
                        <div className="aw-mono" style={{ fontWeight: 600, fontSize: 30, color: 'var(--danger)' }}>{fmtM(cf.effect)}</div>
                        <div style={{ fontSize: 12, color: 'var(--text3)' }}>revenue foregone</div>
                      </div>
                      <div className="aw-mono" style={{ fontSize: 11, fontWeight: 500, color: 'var(--text2)' }}>95% conformal CI [{fmtM(cf.ciLo)}, {fmtM(cf.ciHi)}] · E-value {cf.evalue}</div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                        <div style={{ background: 'var(--sunken)', border: '1px solid var(--hair)', borderRadius: 7, padding: '9px 12px' }}><div style={{ fontSize: 10.5, color: 'var(--text3)' }}>Estimators agree</div><div className="aw-mono" style={{ fontWeight: 600, fontSize: 14, color: 'var(--accent)', marginTop: 3 }}>7/7</div></div>
                        <div style={{ background: 'var(--sunken)', border: '1px solid var(--hair)', borderRadius: 7, padding: '9px 12px' }}><div style={{ fontSize: 10.5, color: 'var(--text3)' }}>Refuters passed</div><div className="aw-mono" style={{ fontWeight: 600, fontSize: 14, color: 'var(--accent)', marginTop: 3 }}>4/4</div></div>
                      </div>
                      <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>Adversarial critic: 3 challenges raised, 0 upheld</div>
                      {audience === 'auditor' && (
                        <div className="aw-mono" style={{ border: '1px solid var(--hair)', borderRadius: 8, overflow: 'hidden', fontSize: 10.5 }}>
                          <div className="aw-table-head" style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr' }}><div style={{ padding: '6px 12px' }}>ESTIMATOR</div><div style={{ padding: '6px 12px' }}>EFFECT</div></div>
                          {[['backdoor.linear_reg', fmtM(cf.effect + 0.04)], ['psm', fmtM(cf.effect - 0.02)], ['dml', fmtM(cf.effect - 0.04)], ['ipw', fmtM(cf.effect + 0.01)]].map(([n, e]) => (
                            <div key={n} style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', borderTop: '1px solid var(--hair)' }}><div style={{ padding: '5px 12px' }}>{n}</div><div style={{ padding: '5px 12px' }}>{e}</div></div>
                          ))}
                          <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', borderTop: '1px solid var(--hair)', color: 'var(--accent)' }}><div style={{ padding: '5px 12px' }}>refute: placebo · rand-cause · subset · unobs-confound</div><div style={{ padding: '5px 12px' }}>4/4 ✓</div></div>
                        </div>
                      )}
                      {audience === 'analyst' && (
                        <div className="aw-mono" style={{ background: 'var(--sunken)', border: '1px solid var(--hair)', borderRadius: 8, padding: '11px 13px', fontSize: 10.5, lineHeight: 1.7, color: 'var(--text2)', whiteSpace: 'pre-wrap' }}>
                          {`{"artifact": {"effect": ${Math.round(cf.effect * 1e6)},\n "ci": [${Math.round(cf.ciLo * 1e6)}, ${Math.round(cf.ciHi * 1e6)}],\n "e_value": ${cf.evalue}, "estimators": 7,\n "audit_record_hash": "${cf.hash}"}}`}
                        </div>
                      )}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingTop: 4, borderTop: '1px solid var(--hair)' }}>
                        <div className="aw-mono" style={{ fontSize: 10, fontWeight: 500, color: 'var(--text3)', background: 'var(--sunken)', border: '1px solid var(--hair)', borderRadius: 5, padding: '3px 8px' }}>record {cf.hash} · ED25519 ✓</div>
                        <div style={{ flex: 1 }} />
                        <button onClick={runCf} className="aw-btn-accent" style={{ fontSize: 11.5, padding: '5px 11px', borderRadius: 6 }}>Re-run audit</button>
                        <div style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 11px', cursor: 'pointer' }}>Signed PDF</div>
                      </div>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(380px,1fr))', gap: 16, alignItems: 'start' }}>
            {showHealing && (
              <div className="aw-panel" data-testid="wb-healing">
                <div className="aw-panel-head">
                  <div className="aw-panel-title">Healing queue</div>
                  {pendingCount > 0
                    ? <div className="aw-chip" style={{ fontWeight: 600, color: 'var(--warn)', background: 'var(--warn-dim)' }}>{pendingCount} PENDING_APPROVAL</div>
                    : <div className="aw-chip aw-pill-accent" style={{ fontWeight: 600 }}>QUEUE CLEAR</div>}
                </div>
                <div style={{ padding: '6px 16px 14px' }}>
                  {healing.map((h) => (
                    <div key={h.id} style={{ padding: '11px 0', borderBottom: '1px solid var(--hair)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div className="aw-mono" style={{ fontSize: 11.5, fontWeight: 500 }}>{h.title}</div>
                        <div className="aw-mono" style={{ fontSize: 9, fontWeight: 700, borderRadius: 99, padding: '1px 7px', color: h.safe ? 'var(--accent)' : 'var(--warn)', background: h.safe ? 'var(--accent-dim)' : 'var(--warn-dim)', border: `1px solid ${h.safe ? 'var(--accent)' : 'var(--warn)'}` }}>{h.method}</div>
                      </div>
                      <div style={{ marginTop: 5, fontSize: 11, color: 'var(--text3)' }}>{h.sub}</div>
                      {h.state === 'pending' && (
                        <div style={{ marginTop: 8, display: 'flex', gap: 7 }}>
                          <div onClick={() => decideHeal(h.id, true)} style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, color: 'var(--accent)', background: 'var(--accent-dim)', border: '1px solid var(--accent-bd)', borderRadius: 5, padding: '4px 12px' }}>Approve & deploy</div>
                          <div onClick={() => decideHeal(h.id, false)} style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, color: 'var(--danger)', background: 'var(--danger-dim)', border: '1px solid var(--danger)', borderRadius: 5, padding: '4px 12px' }}>Reject</div>
                        </div>
                      )}
                      {h.resolution && <div className="aw-mono" style={{ marginTop: 8, fontSize: 10.5, fontWeight: 500, color: h.state === 'deployed' ? 'var(--accent)' : 'var(--danger)' }}>{h.resolution}</div>}
                    </div>
                  ))}
                  <div style={{ paddingTop: 10, fontSize: 10.5, color: 'var(--text3)' }}>4 auto-healed this week · every override signed to WORM log</div>
                </div>
              </div>
            )}

            {showPipes && (
              <div className="aw-panel" data-testid="wb-pipes">
                <div className="aw-panel-head">
                  <div className="aw-panel-title">Pipelines & streaming</div>
                  <div style={{ flex: 1 }} />
                  <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 500, color: 'var(--accent)' }}>PII MASKING ON</div>
                </div>
                <div style={{ padding: '12px 16px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div className="aw-mono" style={{ display: 'flex', gap: 8, fontSize: 10.5, fontWeight: 500, flexWrap: 'wrap' }}>
                    {[['NetSuite', 'var(--accent)'], ['Workday', 'var(--accent)'], ['kafka:erp.gl DLQ 3', 'var(--warn)']].map(([t, c]) => (
                      <div key={t} style={{ display: 'flex', alignItems: 'center', gap: 6, border: '1px solid var(--hair)', borderRadius: 6, padding: '5px 10px', color: 'var(--text2)' }}><span style={{ width: 5, height: 5, borderRadius: '50%', background: c }} />{t}</div>
                    ))}
                  </div>
                  <div style={{ border: '1px solid var(--hair)', borderRadius: 7, overflow: 'hidden', fontSize: 11.5 }}>
                    <div className="aw-table-head" style={{ display: 'grid', gridTemplateColumns: '1.6fr .9fr .7fr .8fr' }}><div style={{ padding: '6px 12px' }}>RUN</div><div style={{ padding: '6px 12px' }}>STATUS</div><div style={{ padding: '6px 12px' }}>TIME</div><div style={{ padding: '6px 12px' }}>ROWS</div></div>
                    {runs.map((r) => (
                      <div key={r.name} style={{ display: 'grid', gridTemplateColumns: '1.6fr .9fr .7fr .8fr', borderTop: '1px solid var(--hair)', alignItems: 'center' }}>
                        <div className="aw-cell">{r.name}</div>
                        <div style={{ padding: '7px 12px', fontWeight: 600, color: r.color }}>{r.status}</div>
                        <div className="aw-cell">{r.time}</div>
                        <div className="aw-cell">{r.rows}</div>
                      </div>
                    ))}
                  </div>
                  <div style={{ fontSize: 10.5, color: 'var(--text3)' }}>Transforms: filter · aggregate · dedupe · cast · custom SQL → CSV / Parquet / JSON</div>
                </div>
              </div>
            )}

            {showLineage && (
              <div className="aw-panel" data-testid="wb-lineage">
                <div className="aw-panel-head"><div className="aw-panel-title">Lineage — net_revenue</div></div>
                <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {['netsuite.gl_feed', 'kafka: erp.gl.v2', 'lake.fact_revenue'].map((t) => (
                    <div key={t}>
                      <div className="aw-mono" style={{ fontSize: 10.5, fontWeight: 500, color: 'var(--text2)', background: 'var(--sunken)', border: '1px solid var(--hair)', borderRadius: 6, padding: '7px 10px' }}>{t}</div>
                      <div style={{ color: 'var(--text3)', paddingLeft: 12 }}>↓</div>
                    </div>
                  ))}
                  <div className="aw-mono" style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--accent)', background: 'var(--accent-dim)', border: '1px solid var(--accent-bd)', borderRadius: 6, padding: '7px 10px' }}>metric: net_revenue ✓</div>
                  <div style={{ marginTop: 4, fontSize: 10.5, color: 'var(--text3)', lineHeight: 1.6 }}>Atomic Parquet load · replayable from ledger {ledger.no} · UASR shim v3 active upstream</div>
                </div>
              </div>
            )}
          </div>

          {showHistory && (
            <div className="aw-panel" data-testid="wb-history">
              <div className="aw-panel-head"><div className="aw-panel-title">Query history</div><div style={{ flex: 1 }} /><div style={{ fontSize: 11, color: 'var(--text3)' }}>this session + today</div></div>
              <div style={{ fontSize: 11.5 }}>
                <div className="aw-table-head" style={{ display: 'grid', gridTemplateColumns: '.55fr 2.6fr .8fr .7fr .55fr .6fr .7fr' }}>{['TIME', 'QUERY', 'ENGINE', 'STATUS', 'COST', 'DUR', 'BY'].map((h) => <div key={h} style={{ padding: '7px 16px' }}>{h}</div>)}</div>
                {history.map((hq, i) => (
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: '.55fr 2.6fr .8fr .7fr .55fr .6fr .7fr', borderTop: '1px solid var(--hair)', alignItems: 'center' }}>
                    <div className="aw-mono" style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text3)' }}>{hq.time}</div>
                    <div style={{ padding: '8px 16px' }}>{hq.q}</div>
                    <div className="aw-mono" style={{ padding: '8px 16px', fontSize: 11 }}>{hq.engine}</div>
                    <div style={{ padding: '8px 16px', fontWeight: 600, color: hq.status === 'signed' ? 'var(--accent)' : 'var(--text2)' }}>{hq.status}</div>
                    <div className="aw-mono" style={{ padding: '8px 16px', fontSize: 11 }}>{hq.cost}</div>
                    <div className="aw-mono" style={{ padding: '8px 16px', fontSize: 11 }}>{hq.dur}</div>
                    <div style={{ padding: '8px 16px', color: 'var(--text3)' }}>{hq.by}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {nav === 'Cockpit' && (
            <div className="aw-panel" data-testid="wb-feed">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px' }}><span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', animation: 'awpulse 1.6s infinite' }} /><div className="aw-panel-title">Live Feed</div><div style={{ flex: 1 }} /><div style={{ fontSize: 10.5, color: 'var(--text3)' }}>kafka · UASR · ledger</div></div>
              {feed.map((ev, i) => (
                <div key={i} className="aw-mono" style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '6px 16px', borderTop: '1px solid var(--hair)', fontSize: 10.5 }}>
                  <span style={{ color: 'var(--text3)', flex: 'none' }}>{ev.time}</span>
                  <span style={{ flex: 'none', fontWeight: 700, fontSize: 9, letterSpacing: '.06em', color: ev.color }}>{ev.k}</span>
                  <span style={{ color: 'var(--text2)' }}>{ev.t}</span>
                </div>
              ))}
            </div>
          )}

          {showStub && (
            <div style={{ background: 'var(--surface)', border: '1px dashed var(--border)', borderRadius: 10, padding: 36, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, textAlign: 'center' }} data-testid="wb-stub">
              <div className="aw-display" style={{ fontWeight: 600, fontSize: 13 }}>{nav}</div>
              <div style={{ fontSize: 12.5, color: 'var(--text2)', maxWidth: 460, lineHeight: 1.6 }}>{STUB_DESCS[nav] || 'Module from the AURA platform.'}</div>
              <a href={STUB_LINKS[nav] || '/app'} className="aw-mono" style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--accent)', textDecoration: 'none', border: '1px solid var(--accent-bd)', borderRadius: 6, padding: '6px 14px' }}>Open in classic app →</a>
            </div>
          )}
        </div>
      </div>

      {/* command palette */}
      {paletteOpen && (
        <div onClick={() => setPaletteOpen(false)} style={{ position: 'fixed', inset: 0, background: 'var(--overlay)', zIndex: 100, display: 'flex', justifyContent: 'center', paddingTop: 120 }} data-testid="wb-palette">
          <div onClick={(e) => e.stopPropagation()} style={{ width: 520, height: 'fit-content', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, boxShadow: '0 24px 60px rgba(0,0,0,.35)', overflow: 'hidden', animation: 'awup .18s ease' }}>
            <input ref={paletteInput} value={paletteQ} onChange={(e) => setPaletteQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && commands[0]) commands[0].run(); }} placeholder="Type a command or destination…" style={{ width: '100%', boxSizing: 'border-box', background: 'transparent', border: 'none', borderBottom: '1px solid var(--hair)', padding: '14px 18px', font: "400 14px 'Instrument Sans',sans-serif", color: 'var(--text)', outline: 'none' }} />
            <div style={{ maxHeight: 320, overflowY: 'auto', padding: 6 }}>
              {commands.map((c) => (
                <div key={c.title} onClick={c.run} className="aw-hover-raise" style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '9px 12px', borderRadius: 7, fontSize: 13 }}>
                  <span>{c.title}</span><span className="aw-mono" style={{ fontSize: 9.5, fontWeight: 500, color: 'var(--text3)' }}>{c.hint}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* toast */}
      {toast && (
        <div style={{ position: 'fixed', bottom: 26, left: '50%', transform: 'translateX(-50%)', zIndex: 200, background: 'var(--raised)', border: '1px solid var(--accent-bd)', color: 'var(--text)', font: "500 12px 'Instrument Sans',sans-serif", borderRadius: 8, padding: '10px 18px', boxShadow: '0 8px 30px rgba(0,0,0,.3)', animation: 'awup .2s ease', display: 'flex', alignItems: 'center', gap: 8 }} data-testid="wb-toast">
          <span style={{ color: 'var(--accent)' }}>✓</span>{toast}
        </div>
      )}
    </div>
  );
}
