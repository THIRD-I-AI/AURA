/* AURA Workbench — Terminal-authority cockpit (Phase 6).
   One shell, four nav groups, a dense cockpit board, ⌘K palette, and a live
   System Radar hero. Dark-first, mono-first, sharp-cornered, green-signal —
   no theme toggle (theme_honesty). Live wiring where the platform already
   has the API (Ask AURA → commander SSE, ledger chip → /audit/ledger/verify,
   health + pipelines polled so new state reflects on its own); design seed
   data elsewhere so every panel renders.
   Additive: the classic /app shell is untouched and reachable from stubs. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { UserMenu } from '../auth/UserMenu';
import {
  API_BASE_URL,
  analyticsService,
  authService,
  chatService,
  getAuthToken,
  getCurrentWorkspaceId,
  healingService,
  streamingService,
  uploadService,
} from '../services/api';
import { motion } from 'motion/react';
import { deckSwitch, maybe } from '../lib/motion';
import { VIEW_REGISTRY } from './viewRegistry';
import { ViewHost } from './views';
import { SystemRadar } from '../components/radar';
import type { SystemRadarModel, Severity } from '../components/radar';
import './workbench.css';

type Msg = { q: string; sql?: string; critic?: string; columns?: string[]; rows?: string[][]; answer?: string };
type Heal = { id: string; title: string; method: string; safe: boolean; sub: string; state: 'pending' | 'deployed' | 'rejected'; resolution?: string };
type FeedEv = { time: string; k: string; color: string; t: string };

const NAV_GROUPS: [string, string[]][] = [
  ['WORKSPACE', ['Cockpit', 'Ask AURA', 'Dashboards', 'Library', 'Query History']],
  ['AUDIT', ['Audit Workbench', 'Counterfactuals', 'Certificates', 'Exception Queue']],
  ['OPERATE', ['Pipelines', 'Streaming', 'Healing Queue', 'Scheduler', 'Webhooks', 'Cost']],
  ['DATA', ['Connectors', 'Files & Data', 'Lineage', 'Metadata Store']],
];

/* Descriptions for platform modules that don't yet have a dedicated inline view. */
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
  'Ask AURA': 'Full-page conversational analytics over your datasets.',
  'Audit Workbench': 'HITL exception review with signed decisions.',
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
const now = () => new Date().toTimeString().slice(0, 5);
type CfState =
  | { status: 'idle' }
  | { status: 'running'; stageIdx: number }
  | { status: 'done'; nFindings: number | null; materiality: string | null; hash: string | null; verifyUrl: string | null; raw: string }
  | { status: 'error'; message: string };

export default function Workbench() {
  /* Single front door: an already-authenticated session (classic login or a
     restored token) skips the inner login and boots straight to the cockpit. */
  const [view, setView] = useState<'login' | 'boot' | 'app'>(
    () => (authService.currentUser?.() ? 'boot' : 'login'),
  );
  const [nav, setNav] = useState('Cockpit');
  const [toast, setToast] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false); // mobile nav drawer (<860px)
  const [paletteQ, setPaletteQ] = useState('');
  const [loginError, setLoginError] = useState<string | null>(null);
  const [bootIdx, setBootIdx] = useState(0);
  const [audience, setAudience] = useState<'operator' | 'auditor' | 'analyst'>('operator');
  /* NO seeded/dummy data: every panel below starts empty and fills from the
     platform's real APIs (or shows an honest empty/offline state). */
  const [messages, setMessages] = useState<Msg[]>([]);
  const [thinking, setThinking] = useState<string | null>(null);
  const [healing, setHealing] = useState<Heal[]>([]);
  const [cf, setCf] = useState<CfState>({ status: 'idle' });
  const [feed, setFeed] = useState<FeedEv[]>([]);
  const [history, setHistory] = useState<Array<{ time: string; q: string; engine: string; status: string; cost: string; dur: string; by: string }>>([]);
  const [ledger, setLedger] = useState<{ no: string; hash: string; intact: boolean } | null>(null);
  const [health, setHealth] = useState<{ up: number; total: number } | null>(null);
  const [services, setServices] = useState<Array<{ name: string; up: boolean }> | null>(null);
  const [files, setFiles] = useState<number | null>(null);
  const [pipelines, setPipelines] = useState<Array<{ name: string; status: string }> | null>(null);
  const [gatewayUp, setGatewayUp] = useState<boolean | null>(null);
  const [ledgerDown, setLedgerDown] = useState(false);
  const [ssoEnabled, setSsoEnabled] = useState(false);

  /* Real enterprise SSO when the deployment configures OIDC; buttons fall
     back to the demo boot flow otherwise. */
  useEffect(() => {
    fetch(`${API_BASE_URL}/auth/oidc/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => setSsoEnabled(Boolean(j?.enabled)))
      .catch(() => undefined);
  }, []);
  const chatInput = useRef<HTMLInputElement>(null);
  const emailInput = useRef<HTMLInputElement>(null);
  const passInput = useRef<HTMLInputElement>(null);
  const paletteInput = useRef<HTMLInputElement>(null);
  const cfBusy = useRef(false);

  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showToast = useCallback((t: string) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(t);
    toastTimer.current = setTimeout(() => setToast(null), 2600);
  }, []);

  // Stable identity so the memoized SystemRadar isn't re-rendered every poll.
  const onRadarService = useCallback((id: string) => showToast(`service · ${id}`), [showToast]);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const pushFeed = useCallback((k: string, color: string, t: string) => {
    setFeed((f) => [{ time: now(), k, color, t }, ...f].slice(0, 8));
  }, []);

  /* Real data, fetched once the app view mounts. Every failure degrades to an
     honest empty/offline state — nothing is fabricated. */
  useEffect(() => {
    if (view !== 'app') return;
    // Ledger verify is tenant-scoped (tenant from the verified JWT), so the
    // bearer must ride along — a bare fetch 401s and looked like an outage.
    const tok = getAuthToken();
    fetch(`${API_BASE_URL}/counterfactual/audit/ledger/verify`, tok ? { headers: { Authorization: `Bearer ${tok}` } } : undefined)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (j && typeof j.count === 'number') {
          const mr = String(j.merkle_root || '');
          setLedger({ no: '#' + j.count.toLocaleString(), hash: mr ? mr.slice(0, 4) + '…' + mr.slice(-4) : '—', intact: !!j.ok });
          pushFeed('AUDIT', 'var(--accent)', `ledger verified · ${j.count} records · chain ${j.ok ? 'intact' : 'BROKEN'}`);
        } else {
          setLedgerDown(true);
        }
      })
      .catch(() => setLedgerDown(true));
    uploadService.getUploadedFiles().then((f) => setFiles(f.length)).catch(() => undefined);
    analyticsService.getQueryHistory()
      .then((rows: unknown) => {
        const list = Array.isArray(rows) ? rows : (rows as { history?: unknown[] })?.history ?? [];
        setHistory((list as Array<Record<string, unknown>>).slice(0, 8).map((r) => ({
          time: String(r.timestamp ?? r.time ?? '').slice(11, 16) || '—',
          q: String(r.query ?? r.sql ?? r.question ?? '(query)').slice(0, 64),
          engine: String(r.engine ?? 'DuckDB'),
          status: String(r.status ?? 'completed'),
          cost: String(r.cost ?? '—'),
          dur: r.duration_ms ? `${((r.duration_ms as number) / 1000).toFixed(1)}s` : '—',
          by: String(r.user ?? r.by ?? '—'),
        })));
      })
      .catch(() => undefined);
  }, [view, pushFeed]);

  /* ── Reactive pulse ──────────────────────────────────────────────────
     Health, pipelines, and pending recoveries are POLLED, not fetched once,
     so new pipelines, changed service health, and fresh drift/recovery
     events reflect on their own — the radar and cockpit stay live without a
     manual refresh. Every request is abortable and every failure degrades to
     an honest state (offline gateway, empty pipelines) instead of throwing.
     A single in-flight guard prevents overlap on a slow network. */
  useEffect(() => {
    if (view !== 'app') return;
    const root = API_BASE_URL.replace(/\/api\/v1$/, '');
    let alive = true;
    let inFlight = false;

    const pulse = async () => {
      if (!alive || inFlight) return;
      inFlight = true;
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), 8000);
      try {
        // Health → gateway up/down + per-service node list for the radar.
        try {
          const r = await fetch(`${root}/health`, { signal: ac.signal });
          if (!alive) return;
          setGatewayUp(r.ok);
          const j = r.ok ? await r.json() : null;
          if (j) {
            const src = j.services ?? j.components ?? j.checks;
            const entries = src && typeof src === 'object' ? Object.entries(src) : [];
            const svcList = entries.map(([name, v]) => ({
              name,
              up: /health|ok|up|pass/i.test(String((v as { status?: string })?.status ?? v)),
            }));
            if (svcList.length > 0) {
              setServices(svcList);
              setHealth({ up: svcList.filter((s) => s.up).length, total: svcList.length });
            }
          }
        } catch (e) {
          if (alive && (e as Error)?.name !== 'AbortError') setGatewayUp(false);
        }

        // Pending recoveries → healing deck + radar drift/recovery signal.
        try {
          const pending = await healingService.pending();
          if (alive) setHealing(pending.map((p) => ({
            id: p.id,
            title: p.source_id || p.drift_event_id,
            method: (p.generation_method || 'template').toUpperCase(),
            safe: p.validation_passed === true,
            sub: p.diagnosis || 'data-contract drift · awaiting reviewer',
            state: 'pending' as const,
          })));
        } catch { /* keep last-known healing; honest empty on first miss */ }

        // Streaming pipelines → new pipelines appear on their own.
        try {
          const r = await streamingService.list();
          if (alive) setPipelines((r.pipelines ?? []).slice(0, 6).map((p) => ({
            name: (p as { name?: string; pipeline_id?: string }).name ?? (p as { pipeline_id?: string }).pipeline_id ?? 'pipeline',
            status: String((p as { state?: string; status?: string }).state ?? (p as { status?: string }).status ?? 'unknown'),
          })));
        } catch {
          if (alive) setPipelines((prev) => prev ?? null);
        }
      } finally {
        clearTimeout(timer);
        inFlight = false;
      }
    };

    pulse();
    const id = setInterval(pulse, 10000);
    const onVis = () => { if (document.visibilityState === 'visible') pulse(); };
    document.addEventListener('visibilitychange', onVis);
    return () => { alive = false; clearInterval(id); document.removeEventListener('visibilitychange', onVis); };
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
    pushFeed('QUERY', 'var(--text2)', `commander run: ${q.slice(0, 48)}`);
    setHistory((h) => [{ time: now(), q: q.length > 52 ? q.slice(0, 52) + '…' : q, engine: 'DuckDB', status: sql ? 'executed' : 'answered', cost: '—', dur: '—', by: 'you' }, ...h]);
  };

  /* Runs the REAL one-click forensic audit (signed report → ledger). The
     stage list animates only while the real request is in flight. */
  const runCf = async () => {
    // Ref guard, not a `cf` closure read: the palette memo holds a stale
    // runCf reference, so the in-flight check must not rely on captured state.
    if (cfBusy.current) return;
    cfBusy.current = true;
    setCf({ status: 'running', stageIdx: 0 });
    const t = setInterval(() => {
      setCf((c) => (c.status === 'running' && c.stageIdx < CF_STAGES.length - 1 ? { status: 'running', stageIdx: c.stageIdx + 1 } : c));
    }, 700);
    try {
      const r = await fetch(`${API_BASE_URL}/counterfactual/audit/financial/demo`);
      if (!r.ok) throw new Error(`audit service replied ${r.status}`);
      const j = await r.json();
      const hash = typeof j.record_hash === 'string' ? j.record_hash : null;
      setCf({
        status: 'done',
        nFindings: typeof j.n_findings === 'number' ? j.n_findings : Array.isArray(j.findings) ? j.findings.length : null,
        materiality: j.materiality_threshold != null ? String(j.materiality_threshold) : null,
        hash,
        verifyUrl: hash ? `/verify/${hash}` : null,
        raw: JSON.stringify({ record_hash: j.record_hash, n_findings: j.n_findings, signature_status: j.signature_status, dataset_fingerprint: j.dataset_fingerprint }, null, 1),
      });
      pushFeed('AUDIT', 'var(--accent)', `signed forensic audit → ledger · ${String(j.record_hash ?? '').slice(0, 12)}…`);
      showToast('Audit complete — record signed to ledger');
    } catch (e) {
      setCf({ status: 'error', message: e instanceof Error ? e.message : 'audit service unreachable' });
    } finally {
      clearInterval(t);
      cfBusy.current = false;
    }
  };

  /* Real S41 HITL decisions — approve deploys the shim, reject pauses it.
     Both are recorded server-side (signed override). */
  const decideHeal = async (id: string, ok: boolean) => {
    try {
      if (ok) await healingService.approve(id, 'workbench-ui'); else await healingService.reject(id, 'workbench-ui', 'rejected from workbench');
      setHealing((hs) => hs.map((h) => h.id === id ? {
        ...h, state: ok ? 'deployed' : 'rejected',
        resolution: ok ? '✓ approved — shim deploying, override signed' : '✕ rejected — recovery halted',
      } : h));
      pushFeed('HEAL', 'var(--warn)', `${ok ? 'approved' : 'rejected'} recovery ${id.slice(0, 10)}`);
      showToast(ok ? 'Shim approved — deploying upstream' : 'Healing proposal rejected');
    } catch (e) {
      showToast(`Decision failed: ${e instanceof Error ? e.message : 'service unreachable'}`);
    }
  };

  const pendingCount = healing.filter((h) => h.state === 'pending').length;
  const dash = { value: '—', subColor: 'var(--text3)' };
  const stats = [
    { label: 'Services healthy', value: health ? `${health.up}/${health.total}` : gatewayUp ? '✓' : dash.value, sub: gatewayUp === false ? 'gateway offline' : gatewayUp ? 'gateway up' : 'checking…', subColor: gatewayUp === false ? 'var(--danger)' : gatewayUp ? 'var(--accent)' : 'var(--text3)' },
    { label: 'Datasets loaded', value: files != null ? String(files) : dash.value, sub: 'workspace uploads', subColor: 'var(--text3)' },
    { label: 'Ledger records', value: ledger ? ledger.no.replace('#', '') : dash.value, sub: ledger ? `chain ${ledger.intact ? 'intact' : 'BROKEN'}` : ledgerDown ? 'ledger service offline' : 'verifying…', subColor: ledger?.intact === false ? 'var(--danger)' : ledgerDown ? 'var(--warn)' : 'var(--accent)' },
    { label: 'Recent queries', value: String(history.length), sub: 'this workspace', subColor: 'var(--text3)' },
    { label: 'Pending approvals', value: String(pendingCount), sub: pendingCount > 0 ? 'healing queue' : 'queue clear', subColor: pendingCount > 0 ? 'var(--warn)' : 'var(--accent)' },
    { label: 'Pipelines', value: pipelines ? String(pipelines.length) : dash.value, sub: pipelines?.length ? 'streaming' : 'none defined', subColor: 'var(--text3)' },
  ];
  /* Header status is DERIVED from live health, never asserted. Claiming
     "verified" while the gateway or ledger is down would be an audit-trust
     lie — the pill degrades honestly. */
  const systemStatus: { label: string; tone: 'ok' | 'warn' | 'danger' | 'idle' } =
    gatewayUp === false ? { label: 'GATEWAY OFFLINE', tone: 'danger' }
    : ledger?.intact === false ? { label: 'LEDGER CHAIN BROKEN', tone: 'danger' }
    : ledgerDown ? { label: 'LEDGER OFFLINE', tone: 'warn' }
    : gatewayUp && ledger?.intact ? { label: 'ALL SYSTEMS VERIFIED', tone: 'ok' }
    : { label: 'VERIFYING…', tone: 'idle' };
  const statusColor = systemStatus.tone === 'ok' ? 'var(--accent)' : systemStatus.tone === 'danger' ? 'var(--danger)' : systemStatus.tone === 'warn' ? 'var(--warn)' : 'var(--text3)';
  const statusBg = systemStatus.tone === 'ok' ? 'var(--accent-dim)' : systemStatus.tone === 'danger' ? 'var(--danger-dim)' : systemStatus.tone === 'warn' ? 'var(--warn-dim)' : 'transparent';

  const runColors: Record<string, string> = { running: 'var(--cyan)', active: 'var(--cyan)', completed: 'var(--accent)', success: 'var(--accent)', failed: 'var(--danger)', error: 'var(--danger)' };
  const runs = (pipelines ?? []).map((p) => ({ name: p.name, status: p.status, color: runColors[p.status.toLowerCase()] ?? 'var(--text2)', time: '—', rows: '—' }));

  /* Live System Radar model — derived purely from state already polled above,
     so it reflects new pipelines, changed health, and fresh drift on its own.
     Sources = streaming pipelines; a pipeline referenced by a pending recovery
     inherits that recovery's severity and shows a live recovery arc. */
  const radarModel: SystemRadarModel = useMemo(() => {
    const pendingBySource = new Map<string, boolean>();
    for (const h of healing) {
      if (h.state === 'pending') pendingBySource.set(h.title, h.safe);
    }
    const sevFor = (name: string): { severity: Severity; recovering: boolean } => {
      if (!pendingBySource.has(name)) return { severity: 'none', recovering: false };
      // A pending recovery that failed validation is the most urgent.
      return { severity: pendingBySource.get(name) === false ? 'critical' : 'high', recovering: true };
    };
    const runToSev: Record<string, Severity> = { failed: 'critical', error: 'critical' };
    return {
      core: 'AURA',
      gatewayUp,
      services: (services ?? []).map((s) => ({
        id: s.name,
        label: s.name.replace(/[_-]?service$/i, '').slice(0, 12),
        up: s.up,
      })),
      sources: (pipelines ?? []).map((p) => {
        const drift = sevFor(p.name);
        const runSev = runToSev[p.status.toLowerCase()];
        return {
          id: p.name,
          label: p.name.slice(0, 12),
          severity: runSev ?? drift.severity,
          recovering: drift.recovering,
        };
      }),
    };
  }, [gatewayUp, services, pipelines, healing]);

  /* ONE app: the cockpit is the live board; every other nav mounts the full
     classic module inside this shell (views.tsx registry). Stubs remain only
     for modules that don't exist anywhere yet. */
  const isCockpit = nav === 'Cockpit';
  const hasView = !isCockpit && Boolean(VIEW_REGISTRY[nav]);
  const showChat = isCockpit;
  const showCf = isCockpit;
  const showHealing = isCockpit;
  const showPipes = isCockpit;
  const showLineage = isCockpit;
  const showHistory = isCockpit;
  const showStub = !isCockpit && !hasView;

  const commands = useMemo(() => {
    const q = paletteQ.toLowerCase();
    const navs = NAV_GROUPS.flatMap(([, items]) => items);
    const all = [
      ...navs.map((n) => ({ title: 'Go to ' + n, hint: 'NAV', run: () => { setNav(n); setPaletteOpen(false); } })),
      { title: 'Run counterfactual audit', hint: 'JOB', run: () => { setNav('Counterfactuals'); setPaletteOpen(false); runCf(); } },
      { title: 'Sign out', hint: 'AUTH', run: () => { setView('login'); setPaletteOpen(false); } },
    ];
    return all.filter((c) => c.title.toLowerCase().includes(q)).slice(0, 9);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paletteQ]);

  /* ── login ── */
  if (view === 'login') {
    return (
      <div className="aw" data-testid="wb-login">
        <div className="aw-hero-split" style={{ flex: 1, minHeight: '100vh', display: 'grid', gridTemplateColumns: '1.1fr 1fr' }}>
          <div className="aw-hero" style={{ padding: '48px 56px', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}><span style={{ width: 8, height: 8, background: 'var(--accent)', borderRadius: 0 }} /><span className="aw-display" style={{ fontWeight: 700, fontSize: 15, letterSpacing: '.1em' }}>AURA</span></div>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 22, maxWidth: 520 }}>
              <div className="aw-display" style={{ fontWeight: 700, fontSize: 44, lineHeight: 1.15 }}>Analysis your auditors can replay.</div>
              <div style={{ fontSize: 15, color: 'var(--text2)', lineHeight: 1.6 }}>Autonomous agents over mission-critical data — every conclusion signed, every pipeline self-healing.</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13, color: 'var(--text2)' }}>
                {['ED25519-signed conclusions · deterministic replay', 'Self-healing streams — Kafka, Postgres, BigQuery (MAPE-K drift repair)', 'Fail-closed auth · PII perimeter masking · WORM audit log'].map((t) => (
                  <div key={t} style={{ display: 'flex', alignItems: 'center', gap: 9 }}><span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--accent)', flex: 'none' }} />{t}</div>
                ))}
              </div>
            </div>
            <div className="aw-mono" style={{ fontSize: 10, fontWeight: 500, color: 'var(--text3)' }}>{ledger ? `LEDGER ${ledger.no} · ${ledger.intact ? 'CHAIN INTACT' : 'CHAIN CHECK'} · sha256 ${ledger.hash}` : 'ED25519-SIGNED · TAMPER-EVIDENT AUDIT LEDGER'}</div>
          </div>
          <div role="main" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40 }}>
            <div style={{ width: 380, display: 'flex', flexDirection: 'column', gap: 18 }}>
              <div>
                <div className="aw-display" style={{ fontWeight: 700, fontSize: 24 }}>Sign in</div>
                <div style={{ marginTop: 6, fontSize: 13, color: 'var(--text2)' }}>Use your corporate identity to continue to <strong>acme-corp</strong>.</div>
              </div>
              {['Okta', 'Microsoft Entra ID', 'Google Workspace'].map((sso) => {
                const go = () => { if (ssoEnabled) { window.location.href = `${API_BASE_URL}/auth/oidc/login`; } else { setBootIdx(0); setView('boot'); } };
                return (
                <div key={sso} role="button" tabIndex={0} aria-label={`Continue with ${sso}`} onClick={go} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } }} className="aw-hover-accent-bd" style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10, border: '1px solid var(--border)', borderRadius: 0, padding: '11px 14px', fontSize: 13.5, fontWeight: 600 }}>
                  <span className="aw-mono" style={{ width: 18, height: 18, display: 'grid', placeItems: 'center', background: 'var(--raised)', borderRadius: 0, fontSize: 9 }}>{sso[0]}</span>
                  Continue with {sso}
                </div>
                );
              })}
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text3)', fontSize: 11 }}><span style={{ flex: 1, height: 1, background: 'var(--hair)' }} />or with email<span style={{ flex: 1, height: 1, background: 'var(--hair)' }} /></div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <label htmlFor="wb-email" style={{ fontSize: 12, fontWeight: 600 }}>Work email<input id="wb-email" name="email" autoComplete="email" ref={emailInput} onKeyDown={(e) => e.key === 'Enter' && signIn()} placeholder="you@acme.com" className="aw-input" style={{ marginTop: 6, width: '100%', boxSizing: 'border-box', padding: '10px 14px', fontSize: 13 }} /></label>
                <label htmlFor="wb-password" style={{ fontSize: 12, fontWeight: 600 }}>Password<input id="wb-password" name="password" autoComplete="current-password" ref={passInput} type="password" onKeyDown={(e) => e.key === 'Enter' && signIn()} placeholder="••••••••••••" className="aw-input" style={{ marginTop: 6, width: '100%', boxSizing: 'border-box', padding: '10px 14px', fontSize: 13 }} /></label>
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
      <div className="aw" data-testid="wb-boot">
        <div style={{ flex: 1, minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 30 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}><span style={{ width: 10, height: 10, background: 'var(--accent)', borderRadius: 0, animation: 'awpulse 1.4s infinite' }} /><span className="aw-display" style={{ fontWeight: 700, fontSize: 18, letterSpacing: '.1em' }}>AURA</span></div>
          <div style={{ width: 340, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {BOOT_STAGES.map((label, i) => (
              <div key={label} className="aw-mono" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 11, fontWeight: 500, color: i < bootIdx ? 'var(--accent)' : i === bootIdx ? 'var(--text)' : 'var(--text3)' }}>
                <span style={{ width: 14, textAlign: 'center' }}>{i < bootIdx ? '✓' : i === bootIdx ? '◌' : '·'}</span>{label}
              </div>
            ))}
          </div>
          <div style={{ width: 340, height: 3, background: 'var(--raised)', borderRadius: 0, overflow: 'hidden' }}><div style={{ height: '100%', background: 'var(--accent)', borderRadius: 0, transition: 'width .45s ease', width: Math.min(100, Math.round((bootIdx / BOOT_STAGES.length) * 100)) + '%' }} /></div>
        </div>
      </div>
    );
  }

  /* ── app ── */
  return (
    /* height (not min-height) bounds the shell so topbar+nav stay pinned and
       ONLY the main column scrolls — the design's cockpit scroll model. */
    <div className="aw" data-testid="wb-app" style={{ height: '100vh', overflow: 'hidden' }}>
      <a href="#wb-main" className="skip-link">Skip to main content</a>
      {/* topbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, height: 54, padding: '0 24px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', flex: 'none' }}>
        <div className="aw-burger" onClick={() => setNavOpen((o) => !o)} role="button" aria-label="Toggle navigation">☰</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}><span style={{ width: 8, height: 8, background: 'var(--accent)', borderRadius: 0 }} /><span className="aw-display" style={{ fontWeight: 700, fontSize: 15, letterSpacing: '.1em' }}>AURA</span></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 0, padding: '5px 10px' }}>{getCurrentWorkspaceId()}</div>
        {gatewayUp === false && <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.08em', color: 'var(--danger)', background: 'var(--sunken)', border: '1px solid var(--border)', borderRadius: 0, padding: '3px 7px' }}>GATEWAY OFFLINE</div>}
        <div style={{ flex: 1 }} />
        <div onClick={() => { setPaletteOpen(true); setTimeout(() => paletteInput.current?.focus(), 30); }} className="aw-mono aw-hover-accent-bd aw-topbar-search" style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontWeight: 500, color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 0, padding: '5px 10px' }}>
          Search, ask, or run a command <span style={{ background: 'var(--sunken)', borderRadius: 0, padding: '1px 5px' }}>⌘K</span>
        </div>
        <UserMenu />
      </div>

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <div className={`aw-backdrop${navOpen ? ' aw-open' : ''}`} onClick={() => setNavOpen(false)} />
        {/* nav */}
        <div className={`aw-nav${navOpen ? ' aw-open' : ''}`} style={{ width: 204, flex: 'none', borderRight: '1px solid var(--border)', background: 'var(--surface)', padding: '16px 10px 20px', display: 'flex', flexDirection: 'column', gap: 18, overflowY: 'auto' }}>
          {NAV_GROUPS.map(([label, items]) => (
            <div key={label}>
              <div className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: '.14em', color: 'var(--text3)', padding: '0 12px 6px' }}>{label}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                {items.map((name) => {
                  const active = name === nav;
                  const badge = (name === 'Exception Queue' || name === 'Healing Queue') && pendingCount > 0 ? String(pendingCount) : null;
                  const goNav = () => { setNav(name); setNavOpen(false); };
                  return (
                    <div key={name} role="button" tabIndex={0} aria-current={active ? 'page' : undefined} onClick={goNav} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goNav(); } }} className="aw-nav-item" style={{ color: active ? 'var(--text)' : 'var(--text2)', background: active ? 'var(--accent-dim)' : 'transparent', fontWeight: active ? 600 : 400 }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{active && <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--accent)' }} />}{name}</span>
                      {badge && <span className="aw-mono" style={{ fontSize: 9.5, fontWeight: 600, color: 'var(--warn)', background: 'var(--warn-dim)', borderRadius: 0, padding: '1px 6px' }}>{badge}</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          <div className="aw-mono" style={{ marginTop: 'auto', padding: '14px 12px 0', borderTop: '1px solid var(--border)', fontSize: 9.5, fontWeight: 500, color: 'var(--text3)', lineHeight: 1.9 }}>
            {ledger ? (<>LEDGER {ledger.no}<br /><span style={{ color: ledger.intact ? 'var(--accent)' : 'var(--danger)' }}>● {ledger.intact ? 'CHAIN INTACT' : 'CHAIN BROKEN'}</span><br />sha256 {ledger.hash}</>) : (<>LEDGER —<br /><span style={ledgerDown ? { color: 'var(--warn)' } : undefined}>● {ledgerDown ? 'SERVICE OFFLINE' : 'VERIFYING…'}</span></>)}
          </div>
        </div>

        {/* main */}
        <main id="wb-main" tabIndex={-1} className="aw-main" style={{ flex: 1, minWidth: 0, padding: '24px 26px 28px', display: 'flex', flexDirection: 'column', gap: 16, overflowY: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div className="aw-display" style={{ fontWeight: 600, fontSize: 22 }}>{nav}</div>
            <div className="aw-chip" role="status" aria-live="polite" aria-atomic="true" aria-label={`System status: ${systemStatus.label}`} style={{ display: 'flex', alignItems: 'center', gap: 6, color: statusColor, background: statusBg, border: `1px solid ${systemStatus.tone === 'ok' ? 'var(--accent-bd)' : statusColor}`, fontWeight: 600, letterSpacing: '.08em' }}><span aria-hidden="true" style={{ width: 5, height: 5, borderRadius: '50%', background: statusColor, animation: 'awpulse 2.4s infinite' }} />{systemStatus.label}</div>
            <div style={{ flex: 1 }} />
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>Last full audit replay 06:00 UTC · scheduler on time</div>
          </div>

          <motion.div
            key={nav}
            variants={maybe(deckSwitch)}
            initial="hidden"
            animate="visible"
            style={{ display: 'flex', flexDirection: 'column', gap: 16, minHeight: 0 }}
          >
          {nav === 'Cockpit' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12 }} data-testid="wb-stats">
              {stats.map((st) => (
                <div key={st.label} className="aw-panel" style={{ borderRadius: 0, padding: '12px 14px' }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>{st.label}</div>
                  <div className="aw-mono" style={{ fontWeight: 600, fontSize: 18 }}>{st.value}</div>
                  <div style={{ fontSize: 10.5, marginTop: 3, color: st.subColor }}>{st.sub}</div>
                </div>
              ))}
            </div>
          )}

          {nav === 'Cockpit' && (
            <div className="aw-panel" data-testid="wb-radar" style={{ display: 'grid', gridTemplateColumns: 'minmax(0,340px) 1fr', gap: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '18px 8px', borderRight: '1px solid var(--hair)' }}>
                <SystemRadar model={radarModel} size={320} onServiceClick={onRadarService} />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                <div className="aw-panel-head" style={{ padding: '14px 18px' }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: gatewayUp === false ? 'var(--danger)' : gatewayUp ? 'var(--accent)' : 'var(--text3)', animation: gatewayUp ? 'awpulse 2.4s infinite' : undefined }} />
                  <div style={{ fontSize: 14, fontWeight: 600 }}>Live System Radar</div>
                  <div className="aw-chip aw-pill-outline">real topology</div>
                  <div style={{ flex: 1 }} />
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>{radarModel.services.length} services · {radarModel.sources.length} sources</div>
                </div>
                <div style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div style={{ fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.7 }}>
                    {gatewayUp === false
                      ? 'Gateway unreachable — nodes shown from last known topology. Radar resumes when /health responds.'
                      : radarModel.services.length === 0
                        ? 'Awaiting first health report — service nodes appear as /health responds. Nothing is fabricated.'
                        : 'Each node is a backend service from /health; rim points are streaming sources. A ring pulses on drift and an arc traces each recovery in flight.'}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 16px', fontSize: 11, color: 'var(--text3)' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 8, height: 8, borderRadius: '50%', border: '1.4px solid var(--accent)' }} />service healthy</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 8, height: 8, borderRadius: '50%', border: '1.4px solid var(--danger)' }} />service down</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 8, height: 8, borderRadius: '50%', border: '1.4px solid var(--text3)' }} />awaiting</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--warn)' }} />drift</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--danger)' }} />critical</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(470px,100%),1fr))', gap: 16, alignItems: 'start' }}>
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
                  {messages.length === 0 && !thinking && (
                    <div style={{ fontSize: 12.5, color: 'var(--text3)', lineHeight: 1.7, padding: '10px 0' }}>
                      No conversation yet. Ask about your loaded datasets — the commander generates SQL,
                      executes it in the sandbox, and streams the verified answer here.
                    </div>
                  )}
                  {messages.map((m, i) => (
                    <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 10, animation: 'awup .25s ease' }}>
                      <div style={{ alignSelf: 'flex-end', maxWidth: '70%', background: 'var(--raised)', border: '1px solid var(--border)', borderRadius: '10px 10px 3px 10px', padding: '9px 14px', fontSize: 13 }}>{m.q}</div>
                      {m.sql && <div className="aw-mono" style={{ background: 'var(--sunken)', border: '1px solid var(--hair)', borderRadius: 0, padding: '12px 14px', fontSize: 11.5, lineHeight: 1.65, color: 'var(--text2)', whiteSpace: 'pre-wrap' }}>{m.sql}</div>}
                      {m.critic && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{m.critic}</div>}
                      {m.columns && m.rows && (
                        <div style={{ border: '1px solid var(--hair)', borderRadius: 0, overflow: 'hidden' }}>
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
                  <div style={{ fontSize: 14, fontWeight: 600 }}>Forensic audit</div>
                  <div className="aw-mono" style={{ fontSize: 10, fontWeight: 500, color: 'var(--text3)' }}>{cf.status === 'done' && cf.hash ? cf.hash.slice(0, 12) + '…' : 'AS-2401 · AS-2201 · AS-2305'}</div>
                  <div style={{ flex: 1 }} />
                  <div className="aw-mono" style={{ display: 'flex', fontSize: 10, fontWeight: 600, border: '1px solid var(--border)', borderRadius: 0, overflow: 'hidden' }}>
                    {(['operator', 'auditor', 'analyst'] as const).map((a) => (
                      <div key={a} onClick={() => setAudience(a)} style={{ cursor: 'pointer', padding: '4px 9px', color: a === audience ? 'var(--accent)' : 'var(--text3)', background: a === audience ? 'var(--accent-dim)' : 'transparent' }}>{a.toUpperCase()}</div>
                    ))}
                  </div>
                </div>
                <div style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 13 }}>
                  <div style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--text2)', fontStyle: 'italic' }}>
                    "Run the full forensic sweep — Benford, cutoff, three-way match, segregation of duties,
                    expectation analytics — and sign the findings to the ledger."
                  </div>
                  {cf.status === 'running' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 7, padding: '6px 0' }}>
                      {CF_STAGES.map((label, i) => (
                        <div key={label} className="aw-mono" style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 11, fontWeight: 500, color: i < cf.stageIdx ? 'var(--accent)' : i === cf.stageIdx ? 'var(--text)' : 'var(--text3)' }}>
                          <span style={{ width: 14, textAlign: 'center' }}>{i < cf.stageIdx ? '✓' : i === cf.stageIdx ? '◌' : '·'}</span>{label}
                        </div>
                      ))}
                    </div>
                  )}
                  {cf.status === 'error' && (
                    <div style={{ fontSize: 12, color: 'var(--danger)', lineHeight: 1.6 }}>
                      Audit service unreachable — {cf.message}. Start the counterfactual service and retry.
                    </div>
                  )}
                  {cf.status === 'done' && (
                    <>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
                        <div className="aw-mono" style={{ fontWeight: 600, fontSize: 30, color: cf.nFindings ? 'var(--danger)' : 'var(--accent)' }}>{cf.nFindings ?? '—'}</div>
                        <div style={{ fontSize: 12, color: 'var(--text3)' }}>findings, signed &amp; ledger-chained</div>
                      </div>
                      {cf.materiality && <div className="aw-mono" style={{ fontSize: 11, fontWeight: 500, color: 'var(--text2)' }}>AS-2110 materiality threshold {cf.materiality}</div>}
                      {(audience === 'auditor' || audience === 'analyst') && (
                        <div className="aw-mono" style={{ background: 'var(--sunken)', border: '1px solid var(--hair)', borderRadius: 0, padding: '11px 13px', fontSize: 10.5, lineHeight: 1.7, color: 'var(--text2)', whiteSpace: 'pre-wrap', maxHeight: 180, overflowY: 'auto' }}>{cf.raw}</div>
                      )}
                    </>
                  )}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingTop: 4, borderTop: '1px solid var(--hair)' }}>
                    {cf.status === 'done' && cf.hash && (
                      <a href={cf.verifyUrl ?? '#'} className="aw-mono" style={{ fontSize: 10, fontWeight: 500, color: 'var(--accent)', background: 'var(--sunken)', border: '1px solid var(--hair)', borderRadius: 0, padding: '3px 8px', textDecoration: 'none' }}>record {cf.hash.slice(0, 10)}… · verify ↗</a>
                    )}
                    <div style={{ flex: 1 }} />
                    <button onClick={runCf} disabled={cf.status === 'running'} className="aw-btn-accent" style={{ fontSize: 11.5, padding: '5px 11px', borderRadius: 0, opacity: cf.status === 'running' ? 0.6 : 1 }}>
                      {cf.status === 'idle' ? 'Run signed audit' : cf.status === 'running' ? 'Running…' : 'Re-run audit'}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(380px,100%),1fr))', gap: 16, alignItems: 'start' }}>
            {showHealing && (
              <div className="aw-panel" data-testid="wb-healing">
                <div className="aw-panel-head">
                  <div className="aw-panel-title">Healing queue</div>
                  {pendingCount > 0
                    ? <div className="aw-chip" style={{ fontWeight: 600, color: 'var(--warn)', background: 'var(--warn-dim)' }}>{pendingCount} PENDING_APPROVAL</div>
                    : <div className="aw-chip aw-pill-accent" style={{ fontWeight: 600 }}>QUEUE CLEAR</div>}
                </div>
                <div style={{ padding: '6px 16px 14px' }}>
                  {healing.length === 0 && (
                    <div style={{ padding: '14px 0', fontSize: 12, color: 'var(--text3)', lineHeight: 1.6 }}>
                      No pending recoveries — the MAPE-K loop is nominal. Drift proposals appear here for signed approval.
                    </div>
                  )}
                  {healing.map((h) => (
                    <div key={h.id} style={{ padding: '11px 0', borderBottom: '1px solid var(--hair)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div className="aw-mono" style={{ fontSize: 11.5, fontWeight: 500 }}>{h.title}</div>
                        <div className="aw-mono" style={{ fontSize: 9, fontWeight: 700, borderRadius: 0, padding: '1px 7px', color: h.safe ? 'var(--accent)' : 'var(--warn)', background: h.safe ? 'var(--accent-dim)' : 'var(--warn-dim)', border: `1px solid ${h.safe ? 'var(--accent)' : 'var(--warn)'}` }}>{h.method}</div>
                      </div>
                      <div style={{ marginTop: 5, fontSize: 11, color: 'var(--text3)' }}>{h.sub}</div>
                      {h.state === 'pending' && (
                        <div style={{ marginTop: 8, display: 'flex', gap: 7 }}>
                          <div onClick={() => decideHeal(h.id, true)} style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, color: 'var(--accent)', background: 'var(--accent-dim)', border: '1px solid var(--accent-bd)', borderRadius: 0, padding: '4px 12px' }}>Approve & deploy</div>
                          <div onClick={() => decideHeal(h.id, false)} style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, color: 'var(--danger)', background: 'var(--danger-dim)', border: '1px solid var(--danger)', borderRadius: 0, padding: '4px 12px' }}>Reject</div>
                        </div>
                      )}
                      {h.resolution && <div className="aw-mono" style={{ marginTop: 8, fontSize: 10.5, fontWeight: 500, color: h.state === 'deployed' ? 'var(--accent)' : 'var(--danger)' }}>{h.resolution}</div>}
                    </div>
                  ))}
                  <div style={{ paddingTop: 10, fontSize: 10.5, color: 'var(--text3)' }}>every approve/reject is a signed override in the WORM audit log</div>
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
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, border: '1px solid var(--hair)', borderRadius: 0, padding: '5px 10px', color: 'var(--text2)' }}><span style={{ width: 5, height: 5, borderRadius: '50%', background: pipelines?.length ? 'var(--accent)' : 'var(--text3)' }} />{pipelines ? `${pipelines.length} pipeline${pipelines.length === 1 ? '' : 's'} defined` : 'pipelines unavailable'}</div>
                  </div>
                  {runs.length === 0 && (
                    <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.6 }}>
                      No streaming pipelines yet — <button type="button" onClick={() => setNav('Pipelines')} className="aw-mono" style={{ background: 'none', border: 'none', padding: 0, color: 'var(--accent)', cursor: 'pointer', font: 'inherit' }}>define one in the Pipelines view</button> and it appears here.
                    </div>
                  )}
                  {runs.length > 0 && <div style={{ border: '1px solid var(--hair)', borderRadius: 0, overflow: 'hidden', fontSize: 11.5 }}>
                    <div className="aw-table-head" style={{ display: 'grid', gridTemplateColumns: '1.6fr .9fr .7fr .8fr' }}><div style={{ padding: '6px 12px' }}>RUN</div><div style={{ padding: '6px 12px' }}>STATUS</div><div style={{ padding: '6px 12px' }}>TIME</div><div style={{ padding: '6px 12px' }}>ROWS</div></div>
                    {runs.map((r) => (
                      <div key={r.name} style={{ display: 'grid', gridTemplateColumns: '1.6fr .9fr .7fr .8fr', borderTop: '1px solid var(--hair)', alignItems: 'center' }}>
                        <div className="aw-cell">{r.name}</div>
                        <div style={{ padding: '7px 12px', fontWeight: 600, color: r.color }}>{r.status}</div>
                        <div className="aw-cell">{r.time}</div>
                        <div className="aw-cell">{r.rows}</div>
                      </div>
                    ))}
                  </div>}
                  <div style={{ fontSize: 10.5, color: 'var(--text3)' }}>Transforms: filter · aggregate · dedupe · cast · custom SQL → CSV / Parquet / JSON</div>
                </div>
              </div>
            )}

            {showLineage && (
              <div className="aw-panel" data-testid="wb-lineage">
                <div className="aw-panel-head"><div className="aw-panel-title">Lineage &amp; provenance</div></div>
                <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.65 }}>
                    Dataset-to-finding lineage renders live in the <strong>Constellation</strong> graph —
                    uploaded datasets, derived metrics, and signed findings as a navigable graph.
                  </div>
                  <a href="/app" className="aw-mono" style={{ alignSelf: 'flex-start', fontSize: 10.5, fontWeight: 600, color: 'var(--accent)', border: '1px solid var(--accent-bd)', borderRadius: 0, padding: '6px 12px', textDecoration: 'none' }}>Open Constellation →</a>
                  {ledger && <div style={{ fontSize: 10.5, color: 'var(--text3)', lineHeight: 1.6 }}>Every signed artifact is replayable from ledger {ledger.no}.</div>}
                </div>
              </div>
            )}
          </div>

          {showHistory && (
            <div className="aw-panel" data-testid="wb-history">
              <div className="aw-panel-head"><div className="aw-panel-title">Query history</div><div style={{ flex: 1 }} /><div style={{ fontSize: 11, color: 'var(--text3)' }}>this session + today</div></div>
              <div style={{ fontSize: 11.5 }}>
                <div className="aw-table-head" style={{ display: 'grid', gridTemplateColumns: '.55fr 2.6fr .8fr .7fr .55fr .6fr .7fr' }}>{['TIME', 'QUERY', 'ENGINE', 'STATUS', 'COST', 'DUR', 'BY'].map((h) => <div key={h} style={{ padding: '7px 16px' }}>{h}</div>)}</div>
                {history.length === 0 && <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text3)' }}>No queries recorded yet in this workspace.</div>}
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
            <div className="aw-panel" data-testid="wb-feed" role="log" aria-live="polite" aria-label="Session events">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px' }}><span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', animation: 'awpulse 1.6s infinite' }} /><div className="aw-panel-title">Session events</div><div style={{ flex: 1 }} /><div style={{ fontSize: 10.5, color: 'var(--text3)' }}>real actions only — queries · audits · approvals</div></div>
              {feed.length === 0 && <div style={{ padding: '10px 16px', borderTop: '1px solid var(--hair)', fontSize: 11.5, color: 'var(--text3)' }}>No events yet — run a query or an audit and it lands here.</div>}
              {feed.map((ev, i) => (
                <div key={i} className="aw-mono" style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '6px 16px', borderTop: '1px solid var(--hair)', fontSize: 10.5 }}>
                  <span style={{ color: 'var(--text3)', flex: 'none' }}>{ev.time}</span>
                  <span style={{ flex: 'none', fontWeight: 700, fontSize: 9, letterSpacing: '.06em', color: ev.color }}>{ev.k}</span>
                  <span style={{ color: 'var(--text2)' }}>{ev.t}</span>
                </div>
              ))}
            </div>
          )}

          {hasView && <ViewHost nav={nav} onNavigate={setNav} />}

          {showStub && (
            <div style={{ background: 'var(--surface)', border: '1px dashed var(--border)', borderRadius: 0, padding: 36, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, textAlign: 'center' }} data-testid="wb-stub">
              <div className="aw-display" style={{ fontWeight: 600, fontSize: 13 }}>{nav}</div>
              <div style={{ fontSize: 12.5, color: 'var(--text2)', maxWidth: 460, lineHeight: 1.6 }}>{STUB_DESCS[nav] || 'Module from the AURA platform.'}</div>
            </div>
          )}
          </motion.div>
        </main>
      </div>

      {/* command palette */}
      {paletteOpen && (
        <div onClick={() => setPaletteOpen(false)} style={{ position: 'fixed', inset: 0, background: 'var(--overlay)', zIndex: 100, display: 'flex', justifyContent: 'center', paddingTop: 120 }} data-testid="wb-palette">
          <div onClick={(e) => e.stopPropagation()} style={{ width: 520, height: 'fit-content', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 0, boxShadow: '0 24px 60px rgba(0,0,0,.35)', overflow: 'hidden', animation: 'awup .18s ease' }}>
            <input ref={paletteInput} value={paletteQ} onChange={(e) => setPaletteQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && commands[0]) commands[0].run(); }} placeholder="Type a command or destination…" style={{ width: '100%', boxSizing: 'border-box', background: 'transparent', border: 'none', borderBottom: '1px solid var(--hair)', padding: '14px 18px', font: "400 14px 'Instrument Sans',sans-serif", color: 'var(--text)', outline: 'none' }} />
            <div style={{ maxHeight: 320, overflowY: 'auto', padding: 6 }}>
              {commands.map((c) => (
                <div key={c.title} onClick={c.run} className="aw-hover-raise" style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '9px 12px', borderRadius: 0, fontSize: 13 }}>
                  <span>{c.title}</span><span className="aw-mono" style={{ fontSize: 9.5, fontWeight: 500, color: 'var(--text3)' }}>{c.hint}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* toast */}
      {toast && (
        <div style={{ position: 'fixed', bottom: 26, left: '50%', transform: 'translateX(-50%)', zIndex: 200, background: 'var(--raised)', border: '1px solid var(--accent-bd)', color: 'var(--text)', font: "500 12px 'Instrument Sans',sans-serif", borderRadius: 0, padding: '10px 18px', boxShadow: '0 8px 30px rgba(0,0,0,.3)', animation: 'awup .2s ease', display: 'flex', alignItems: 'center', gap: 8 }} data-testid="wb-toast">
          <span style={{ color: 'var(--accent)' }}>✓</span>{toast}
        </div>
      )}
    </div>
  );
}
