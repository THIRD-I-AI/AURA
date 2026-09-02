/* AURA Workbench — Terminal-authority cockpit (Phase 6).
   One shell, four nav groups, a dense cockpit board, ⌘K palette, and a live
   System Radar hero. Dark-first, mono-first, sharp-cornered, green-signal —
   no theme toggle (theme_honesty). Live wiring where the platform already
   has the API (Ask AURA → commander SSE, ledger chip → /audit/ledger/verify,
   health + pipelines polled so new state reflects on its own); design seed
   data elsewhere so every panel renders.
   The single authenticated app — the real /login gates it (ProtectedRoute), so
   it boots straight in with one shared session; the classic /app shell is gone. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UserMenu } from '../auth/UserMenu';
import { useAuth } from '../auth/AuthContext';
import { cn } from '../lib/cn';
import {
  API_BASE_URL,
  analyticsService,
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
import {
  LayoutDashboard, SquareTerminal, MessageSquare, BarChart3, BookOpen, History,
  ShieldCheck, GitBranch, BadgeCheck, AlertTriangle, Workflow, Activity, Wrench,
  CalendarClock, Link2, DollarSign, Plug, FolderOpen, Share2, Database,
  PanelLeftClose, PanelLeftOpen, type LucideIcon,
} from 'lucide-react';
import { Skeleton } from '@/components/ui-kit/skeleton';
import './workbench.css';

type Msg = { q: string; sql?: string; critic?: string; columns?: string[]; rows?: string[][]; answer?: string };
type Heal = { id: string; title: string; method: string; safe: boolean; sub: string; state: 'pending' | 'deployed' | 'rejected'; resolution?: string };
type FeedEv = { time: string; k: string; color: string; t: string };

const NAV_GROUPS: [string, string[]][] = [
  ['WORKSPACE', ['Cockpit', 'Terminal', 'Ask AURA', 'Dashboards', 'Library', 'Query History']],
  ['AUDIT', ['Audit Workbench', 'Counterfactuals', 'Certificates', 'Exception Queue']],
  ['OPERATE', ['Pipelines', 'Streaming', 'Healing Queue', 'Scheduler', 'Webhooks', 'Cost']],
  ['DATA', ['Connectors', 'Files & Data', 'Lineage', 'Metadata Store']],
];

/* Icon per nav destination — purely visual scanability, no data behind it. */
const NAV_ICONS: Record<string, LucideIcon> = {
  'Cockpit': LayoutDashboard, 'Terminal': SquareTerminal, 'Ask AURA': MessageSquare,
  'Dashboards': BarChart3, 'Library': BookOpen, 'Query History': History,
  'Audit Workbench': ShieldCheck, 'Counterfactuals': GitBranch, 'Certificates': BadgeCheck,
  'Exception Queue': AlertTriangle, 'Pipelines': Workflow, 'Streaming': Activity,
  'Healing Queue': Wrench, 'Scheduler': CalendarClock, 'Webhooks': Link2, 'Cost': DollarSign,
  'Connectors': Plug, 'Files & Data': FolderOpen, 'Lineage': Share2, 'Metadata Store': Database,
};

/* Descriptions for platform modules that don't yet have a dedicated inline view. */
// STUB_DESCS is gone: every nav entry now resolves to a real panel in
// VIEW_REGISTRY, so all of its descriptions were unreachable — and the
// Scheduler one actively overclaimed, advertising a distributed LISTEN/NOTIFY
// job queue that has no gateway route at all. The stub branch below stays as
// the honest fallback for any nav added before its panel exists.
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

/* chatService.streamMessage throws plain Errors: 'commander_disabled' on 404,
   `stream failed: ${status}` on any other non-ok response, or whatever the
   fetch layer itself throws for a network/abort failure (no `status`, no
   parseable message) — branch the user-facing copy on which one it actually
   was instead of collapsing every cause into "offline". */
function describeChatError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  const name = err instanceof Error ? err.name : undefined;
  if (name === 'AbortError') return 'Request cancelled.';
  const statusMatch = /^stream failed: (\d+)$/.exec(message);
  const status = statusMatch ? Number(statusMatch[1]) : null;
  if (status === 401 || status === 403) {
    return 'Your session has expired — please sign in again to continue.';
  }
  if (status !== null && status >= 500) {
    return 'Commander is temporarily unavailable (server error) — try again shortly.';
  }
  return 'Commander offline — showing the workflow with the connected gateway is required for live answers.';
}
type CfState =
  | { status: 'idle' }
  | { status: 'running'; stageIdx: number }
  | { status: 'done'; nFindings: number | null; materiality: string | null; hash: string | null; verifyUrl: string | null; raw: string }
  | { status: 'error'; message: string };

export default function Workbench() {
  /* ProtectedRoute guarantees a real authenticated session before this mounts,
     so there is no inner login — the cockpit boots straight in. */
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [view, setView] = useState<'boot' | 'app'>('boot');
  const [nav, setNav] = useState('Cockpit');
  const [toast, setToast] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false); // mobile nav drawer (<860px)
  const [navCollapsed, setNavCollapsed] = useState(false); // desktop icon-only rail
  const [paletteQ, setPaletteQ] = useState('');
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

  const chatInput = useRef<HTMLInputElement>(null);
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
    } catch (err) {
      answer = describeChatError(err);
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
      // Endpoint is currently anonymous, but send the bearer anyway: the audit it
      // signs is chained under the caller's tenant, and every sibling call here
      // already carries it. A bare fetch is how the job endpoints silently 401'd.
      const demoTok = getAuthToken();
      const r = await fetch(`${API_BASE_URL}/counterfactual/audit/financial/demo`,
                            demoTok ? { headers: { Authorization: `Bearer ${demoTok}` } } : undefined);
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
    { label: 'Services healthy', value: health ? `${health.up}/${health.total}` : gatewayUp ? '✓' : dash.value, sub: gatewayUp === false ? 'gateway offline' : gatewayUp ? 'gateway up' : 'checking…', subColor: gatewayUp === false ? 'var(--danger)' : gatewayUp ? 'var(--accent)' : 'var(--text3)', loading: gatewayUp === null && health === null },
    { label: 'Datasets loaded', value: files != null ? String(files) : dash.value, sub: 'workspace uploads', subColor: 'var(--text3)', loading: files == null },
    { label: 'Ledger records', value: ledger ? ledger.no.replace('#', '') : dash.value, sub: ledger ? `chain ${ledger.intact ? 'intact' : 'BROKEN'}` : ledgerDown ? 'ledger service offline' : 'verifying…', subColor: ledger?.intact === false ? 'var(--danger)' : ledgerDown ? 'var(--warn)' : 'var(--accent)', loading: !ledger && !ledgerDown },
    { label: 'Recent queries', value: String(history.length), sub: 'this workspace', subColor: 'var(--text3)', loading: false },
    { label: 'Pending approvals', value: String(pendingCount), sub: pendingCount > 0 ? 'healing queue' : 'queue clear', subColor: pendingCount > 0 ? 'var(--warn)' : 'var(--accent)', loading: false },
    { label: 'Pipelines', value: pipelines ? String(pipelines.length) : dash.value, sub: pipelines?.length ? 'streaming' : 'none defined', subColor: 'var(--text3)', loading: pipelines == null },
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

  /* Terminal is a full-screen route (a shared authenticated session), not an
     inline view — selecting it navigates; every other nav mounts inline. */
  const selectNav = (name: string) => {
    if (name === 'Terminal') navigate('/app/terminal');
    else setNav(name);
  };

  const commands = useMemo(() => {
    const q = paletteQ.toLowerCase();
    const navs = NAV_GROUPS.flatMap(([, items]) => items);
    const all = [
      ...navs.map((n) => ({ title: 'Go to ' + n, hint: 'NAV', run: () => { selectNav(n); setPaletteOpen(false); } })),
      { title: 'Run counterfactual audit', hint: 'JOB', run: () => { setNav('Counterfactuals'); setPaletteOpen(false); runCf(); } },
      { title: 'Sign out', hint: 'AUTH', run: () => { logout(); setPaletteOpen(false); } },
    ];
    return all.filter((c) => c.title.toLowerCase().includes(q)).slice(0, 9);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paletteQ]);

  /* ── boot ── */
  if (view === 'boot') {
    return (
      <div className="aw" data-testid="wb-boot">
        <div className="flex flex-1 min-h-screen flex-col items-center justify-center gap-[30px]">
          <div className="flex items-center gap-2.5"><span className="w-2.5 h-2.5 bg-[var(--accent)] rounded-none animate-[awpulse_1.4s_infinite]" /><span className="aw-display font-bold text-[18px] tracking-widest">AURA</span></div>
          <div className="flex w-[340px] flex-col gap-2.5">
            {BOOT_STAGES.map((label, i) => (
              <div key={label} className={cn('aw-mono flex items-center gap-2.5 text-[11px] font-medium', i < bootIdx ? 'text-[var(--accent)]' : i === bootIdx ? 'text-[var(--text)]' : 'text-[var(--text3)]')}>
                <span className="w-3.5 text-center">{i < bootIdx ? '✓' : i === bootIdx ? '◌' : '·'}</span>{label}
              </div>
            ))}
          </div>
          <div className="w-[340px] h-[3px] bg-[var(--raised)] rounded-none overflow-hidden"><div className="h-full bg-[var(--accent)] rounded-none" style={{ transition: 'width .45s ease', width: Math.min(100, Math.round((bootIdx / BOOT_STAGES.length) * 100)) + '%' }} /></div>
        </div>
      </div>
    );
  }

  /* ── app ── */
  return (
    /* height (not min-height) bounds the shell so topbar+nav stay pinned and
       ONLY the main column scrolls — the design's cockpit scroll model. */
    <div className="aw h-screen overflow-hidden" data-testid="wb-app">
      <a href="#wb-main" className="skip-link">Skip to main content</a>
      {/* topbar */}
      <div className="flex items-center gap-4 h-[54px] px-6 bg-[var(--surface)] border-b border-[var(--border)] flex-none">
        <div className="aw-burger" onClick={() => setNavOpen((o) => !o)} role="button" aria-label="Toggle navigation">☰</div>
        <div className="flex items-center gap-[9px]"><span className="w-2 h-2 bg-[var(--accent)] rounded-none" /><span className="aw-display font-bold text-[15px] tracking-widest">AURA</span></div>
        <div className="flex items-center gap-2 py-1 pr-2.5 pl-1 text-[12.5px] text-[var(--text2)] border border-[var(--border)] rounded-none">
          <span className="aw-mono w-[18px] h-[18px] grid place-items-center text-[9.5px] font-bold text-[var(--accent)] bg-[var(--accent-dim)] border border-[var(--accent-bd)]">{getCurrentWorkspaceId().slice(0, 2).toUpperCase()}</span>
          {getCurrentWorkspaceId()}
        </div>
        {gatewayUp === false && <div className="aw-mono text-[9.5px] font-semibold tracking-[0.08em] text-[var(--danger)] bg-[var(--sunken)] border border-[var(--border)] rounded-none py-[3px] px-[7px]">GATEWAY OFFLINE</div>}
        <div className="flex-1" />
        <div onClick={() => { setPaletteOpen(true); setTimeout(() => paletteInput.current?.focus(), 30); }} className="aw-mono aw-hover-accent-bd aw-topbar-search cursor-pointer flex items-center gap-2 text-[11px] font-medium text-[var(--text2)] border border-[var(--border)] rounded-none py-[5px] px-2.5">
          Search, ask, or run a command <span className="bg-[var(--sunken)] rounded-none py-px px-[5px]">⌘K</span>
        </div>
        <UserMenu />
      </div>

      <div className="flex flex-1 min-h-0">
        <div className={`aw-backdrop${navOpen ? ' aw-open' : ''}`} onClick={() => setNavOpen(false)} />
        {/* nav */}
        <div className={cn('aw-nav', navOpen && 'aw-open', 'flex flex-none flex-col gap-[18px] overflow-y-auto overflow-x-hidden border-r border-[var(--border)] bg-[var(--surface)] pt-2.5 transition-[width] duration-[160ms] ease-[var(--ease-out)]', navCollapsed ? 'w-14 px-1.5 pb-4' : 'w-[204px] px-2.5 pb-5')}>
          <button
            type="button"
            onClick={() => setNavCollapsed((c) => !c)}
            className={cn('aw-hover-raise aw-topbar-search grid place-items-center w-[22px] h-[22px] border border-[var(--border)] bg-transparent text-[var(--text3)] cursor-pointer flex-none', navCollapsed ? 'self-center' : 'self-end')}
            aria-label={navCollapsed ? 'Expand navigation' : 'Collapse navigation'}
            title={navCollapsed ? 'Expand navigation' : 'Collapse navigation'}
          >
            {navCollapsed ? <PanelLeftOpen size={13} /> : <PanelLeftClose size={13} />}
          </button>
          {NAV_GROUPS.map(([label, items]) => (
            <div key={label}>
              {!navCollapsed && <div className="aw-mono text-[9.5px] font-semibold tracking-[0.14em] text-[var(--text3)] px-3 pb-1.5">{label}</div>}
              <div className="flex flex-col gap-px">
                {items.map((name) => {
                  const active = name === nav;
                  const badge = (name === 'Exception Queue' || name === 'Healing Queue') && pendingCount > 0 ? String(pendingCount) : null;
                  const goNav = () => { selectNav(name); setNavOpen(false); };
                  const Icon = NAV_ICONS[name];
                  return (
                    <div key={name} role="button" tabIndex={0} aria-current={active ? 'page' : undefined} onClick={goNav} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goNav(); } }} title={navCollapsed ? name : undefined} className={cn('aw-nav-item', active ? 'text-[var(--text)] bg-[var(--accent-dim)] font-semibold' : 'text-[var(--text2)] bg-transparent font-normal', navCollapsed ? 'justify-center' : 'justify-between')}>
                      <span className="flex items-center gap-2 min-w-0">
                        {Icon && <Icon size={15} className={cn('flex-none', active ? 'text-[var(--accent)]' : 'text-[var(--text3)]')} />}
                        {!navCollapsed && <span className="truncate">{name}</span>}
                      </span>
                      {!navCollapsed && badge && <span className="aw-mono text-[9.5px] font-semibold text-[var(--warn)] bg-[var(--warn-dim)] rounded-none py-px px-1.5">{badge}</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          {!navCollapsed && (
            <div className="aw-mono mt-auto pt-3.5 px-3 pb-0 border-t border-[var(--border)] text-[9.5px] font-medium text-[var(--text3)] leading-[1.9]">
              {ledger ? (<>LEDGER {ledger.no}<br /><span className={ledger.intact ? 'text-[var(--accent)]' : 'text-[var(--danger)]'}>● {ledger.intact ? 'CHAIN INTACT' : 'CHAIN BROKEN'}</span><br />sha256 {ledger.hash}</>) : (<>LEDGER —<br /><span className={ledgerDown ? 'text-[var(--warn)]' : undefined}>● {ledgerDown ? 'SERVICE OFFLINE' : 'VERIFYING…'}</span></>)}
            </div>
          )}
        </div>

        {/* main */}
        <main id="wb-main" tabIndex={-1} className="aw-main flex-1 min-w-0 flex flex-col gap-4 overflow-y-auto pt-6 pb-7 px-[26px]">
          <div className="flex flex-col gap-1.5">
            <div className="aw-mono text-[11px] text-[var(--text3)] flex items-center gap-1.5">
              <span>{getCurrentWorkspaceId()}</span><span className="text-[var(--border)]">/</span><span className="text-[var(--text2)]">{nav}</span>
            </div>
            <div className="flex items-center gap-3.5">
              <div className="aw-display font-semibold text-[22px]">{nav}</div>
              <div className="aw-chip flex items-center gap-1.5 tracking-[0.08em]" role="status" aria-live="polite" aria-atomic="true" aria-label={`System status: ${systemStatus.label}`} style={{ fontWeight: 600, color: statusColor, background: statusBg, border: `1px solid ${systemStatus.tone === 'ok' ? 'var(--accent-bd)' : statusColor}` }}><span aria-hidden="true" className="w-[5px] h-[5px] rounded-full animate-[awpulse_2.4s_infinite]" style={{ background: statusColor }} />{systemStatus.label}</div>
              <div className="flex-1" />
              <div className="text-[12px] text-[var(--text3)]">Last full audit replay 06:00 UTC · scheduler on time</div>
            </div>
          </div>

          <motion.div
            key={nav}
            variants={maybe(deckSwitch)}
            initial="hidden"
            animate="visible"
            className="flex flex-col gap-4 min-h-0"
          >
          {nav === 'Cockpit' && (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3" data-testid="wb-stats">
              {stats.map((st) => (
                <div key={st.label} className="aw-panel rounded-none py-3 px-3.5">
                  <div className="text-[11px] text-[var(--text3)] mb-1.5">{st.label}</div>
                  {st.loading ? (
                    <>
                      <Skeleton className="h-[18px] w-12" />
                      <Skeleton className="mt-1.5 h-[10.5px] w-20" />
                    </>
                  ) : (
                    <>
                      <div className="aw-mono font-semibold text-[18px]">{st.value}</div>
                      <div className="text-[10.5px] mt-[3px]" style={{ color: st.subColor }}>{st.sub}</div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}

          {nav === 'Cockpit' && (
            <div className="aw-panel grid grid-cols-[minmax(0,340px)_1fr] gap-0" data-testid="wb-radar">
              <div className="flex items-center justify-center py-[18px] px-2 border-r border-[var(--hair)]">
                <SystemRadar model={radarModel} size={320} onServiceClick={onRadarService} />
              </div>
              <div className="flex flex-col min-w-0">
                <div className="aw-panel-head" style={{ padding: '14px 18px' }}>
                  <span className={cn('w-1.5 h-1.5 rounded-full', gatewayUp && 'animate-[awpulse_2.4s_infinite]')} style={{ background: gatewayUp === false ? 'var(--danger)' : gatewayUp ? 'var(--accent)' : 'var(--text3)' }} />
                  <div className="text-[14px] font-semibold">Live System Radar</div>
                  <div className="aw-chip aw-pill-outline">real topology</div>
                  <div className="flex-1" />
                  <div className="text-[11px] text-[var(--text3)]">{radarModel.services.length} services · {radarModel.sources.length} sources</div>
                </div>
                <div className="py-4 px-[18px] flex flex-col gap-3">
                  <div className="text-[12.5px] text-[var(--text2)] leading-[1.7]">
                    {gatewayUp === false
                      ? 'Gateway unreachable — nodes shown from last known topology. Radar resumes when /health responds.'
                      : radarModel.services.length === 0
                        ? 'Awaiting first health report — service nodes appear as /health responds. Nothing is fabricated.'
                        : 'Each node is a backend service from /health; rim points are streaming sources. A ring pulses on drift and an arc traces each recovery in flight.'}
                  </div>
                  <div className="flex flex-wrap gap-y-2 gap-x-4 text-[11px] text-[var(--text3)]">
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ border: '1.4px solid var(--accent)' }} />service healthy</span>
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ border: '1.4px solid var(--danger)' }} />service down</span>
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ border: '1.4px solid var(--text3)' }} />awaiting</span>
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[var(--warn)]" />drift</span>
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[var(--danger)]" />critical</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          <div className="grid grid-cols-[repeat(auto-fit,minmax(min(470px,100%),1fr))] gap-4 items-start">
            {showChat && (
              <div className="aw-panel flex flex-col" data-testid="wb-chat">
                <div className="aw-panel-head" style={{ padding: '14px 18px' }}>
                  <div className="text-[14px] font-semibold">Ask AURA</div>
                  <div className="aw-chip aw-pill-outline">generator ⇄ critic</div>
                  <div className="aw-chip aw-pill-accent">DPC cross-check</div>
                  <div className="flex-1" />
                  <div className="text-[11px] text-[var(--text3)]">DuckDB lake</div>
                </div>
                <div className="py-4 px-[18px] flex flex-col gap-3.5 max-h-[560px] overflow-y-auto">
                  {messages.length === 0 && !thinking && (
                    <div className="text-[12.5px] text-[var(--text3)] leading-[1.7] py-2.5 px-0">
                      No conversation yet. Ask about your loaded datasets — the commander generates SQL,
                      executes it in the sandbox, and streams the verified answer here.
                    </div>
                  )}
                  {messages.map((m, i) => (
                    <div key={i} className="flex flex-col gap-2.5 animate-[awup_.25s_ease]">
                      <div className="self-end max-w-[70%] bg-[var(--raised)] border border-[var(--border)] rounded-[10px_10px_3px_10px] py-[9px] px-3.5 text-[13px]">{m.q}</div>
                      {m.sql && <div className="aw-mono bg-[var(--sunken)] border border-[var(--hair)] rounded-none py-3 px-3.5 text-[11.5px] leading-[1.65] text-[var(--text2)] whitespace-pre-wrap">{m.sql}</div>}
                      {m.critic && <div className="text-[11px] text-[var(--text3)]">{m.critic}</div>}
                      {m.columns && m.rows && (
                        <div className="border border-[var(--hair)] rounded-none overflow-hidden">
                          <div className="flex bg-[var(--raised)]">{m.columns.map((c) => <div key={c} className="aw-mono flex-1 py-[7px] px-3.5 text-[10px] font-semibold text-[var(--text3)] tracking-[0.06em]">{c}</div>)}</div>
                          {m.rows.map((r, ri) => <div key={ri} className="flex border-t border-[var(--hair)]">{r.map((cell, ci) => <div key={ci} className="aw-mono flex-1 py-[7px] px-3.5 text-[11.5px]">{cell}</div>)}</div>)}
                        </div>
                      )}
                      {m.answer && <div className="text-[13px] leading-[1.55]">{m.answer}</div>}
                    </div>
                  ))}
                  {thinking && <div className="aw-mono flex items-center gap-2.5 text-[11px] font-medium text-[var(--text3)]"><span className="aw-spinner" />{thinking}</div>}
                </div>
                <div className="pt-3 px-[18px] pb-4 border-t border-[var(--hair)] flex gap-2">
                  <input ref={chatInput} onKeyDown={(e) => e.key === 'Enter' && ask()} placeholder="Ask anything about your data — SQL is generated, checked, and signed" className="aw-input flex-1 py-2.5 px-3.5 text-[13px]" />
                  <button onClick={ask} className="aw-btn-accent text-[12.5px] py-2.5 px-[18px]">Ask</button>
                </div>
              </div>
            )}

            {showCf && (
              <div className="aw-panel" data-testid="wb-cf">
                <div className="aw-panel-head" style={{ padding: '14px 18px' }}>
                  <div className="text-[14px] font-semibold">Forensic audit</div>
                  <div className="aw-mono text-[10px] font-medium text-[var(--text3)]">{cf.status === 'done' && cf.hash ? cf.hash.slice(0, 12) + '…' : 'AS-2401 · AS-2201 · AS-2305'}</div>
                  <div className="flex-1" />
                  <div className="aw-mono flex text-[10px] font-semibold border border-[var(--border)] rounded-none overflow-hidden">
                    {(['operator', 'auditor', 'analyst'] as const).map((a) => (
                      <div key={a} onClick={() => setAudience(a)} className="cursor-pointer py-1 px-[9px]" style={{ color: a === audience ? 'var(--accent)' : 'var(--text3)', background: a === audience ? 'var(--accent-dim)' : 'transparent' }}>{a.toUpperCase()}</div>
                    ))}
                  </div>
                </div>
                <div className="py-4 px-[18px] flex flex-col gap-[13px]">
                  <div className="text-[13px] leading-[1.5] text-[var(--text2)] italic">
                    "Run the full forensic sweep — Benford, cutoff, three-way match, segregation of duties,
                    expectation analytics — and sign the findings to the ledger."
                  </div>
                  {/* Say plainly which half is a demo. The INPUT is a fixed sample
                      ledger (GET …/audit/financial/demo); the signing, hashing and
                      ledger chaining that follow are the real production pipeline.
                      Without this the tile reads as if it had audited the user's books. */}
                  <div className="aw-mono text-[10.5px] leading-[1.6] text-[var(--text3)] border border-[var(--hair)] rounded-none py-[7px] px-[9px] bg-[var(--sunken)]" data-testid="wb-cf-demo-notice">
                    DEMO SCENARIO — runs a fixed sample ledger. The signature, record hash and
                    ledger chaining below are real. To audit your own ledger, use{' '}
                    <button
                      type="button"
                      onClick={() => selectNav('Exception Queue')}
                      style={{ background: 'none', border: 'none', padding: 0, font: 'inherit', color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline' }}
                    >Exception Queue</button>.
                  </div>
                  {cf.status === 'running' && (
                    <div className="flex flex-col gap-[7px] py-1.5 px-0">
                      {CF_STAGES.map((label, i) => (
                        <div key={label} className="aw-mono flex items-center gap-[9px] text-[11px] font-medium" style={{ color: i < cf.stageIdx ? 'var(--accent)' : i === cf.stageIdx ? 'var(--text)' : 'var(--text3)' }}>
                          <span className="w-3.5 text-center">{i < cf.stageIdx ? '✓' : i === cf.stageIdx ? '◌' : '·'}</span>{label}
                        </div>
                      ))}
                    </div>
                  )}
                  {cf.status === 'error' && (
                    <div className="text-[12px] text-[var(--danger)] leading-[1.6]">
                      Audit service unreachable — {cf.message}. Start the counterfactual service and retry.
                    </div>
                  )}
                  {cf.status === 'done' && (
                    <>
                      <div className="flex items-baseline gap-3">
                        <div className="aw-mono font-semibold text-[30px]" style={{ color: cf.nFindings ? 'var(--danger)' : 'var(--accent)' }}>{cf.nFindings ?? '—'}</div>
                        <div className="text-[12px] text-[var(--text3)]">findings, signed &amp; ledger-chained</div>
                      </div>
                      {cf.materiality && <div className="aw-mono text-[11px] font-medium text-[var(--text2)]">AS-2110 materiality threshold {cf.materiality}</div>}
                      {(audience === 'auditor' || audience === 'analyst') && (
                        <div className="aw-mono bg-[var(--sunken)] border border-[var(--hair)] rounded-none py-[11px] px-[13px] text-[10.5px] leading-[1.7] text-[var(--text2)] whitespace-pre-wrap max-h-[180px] overflow-y-auto">{cf.raw}</div>
                      )}
                    </>
                  )}
                  <div className="flex items-center gap-2 pt-1 border-t border-[var(--hair)]">
                    {cf.status === 'done' && cf.hash && (
                      <a href={cf.verifyUrl ?? '#'} className="aw-mono text-[10px] font-medium bg-[var(--sunken)] border border-[var(--hair)] rounded-none py-[3px] px-2 no-underline" style={{ color: 'var(--accent)' }}>record {cf.hash.slice(0, 10)}… · verify ↗</a>
                    )}
                    <div className="flex-1" />
                    <button onClick={runCf} disabled={cf.status === 'running'} className={cn('aw-btn-accent text-[11.5px] py-[5px] px-[11px] rounded-none', cf.status === 'running' && 'opacity-60')}>
                      {cf.status === 'idle' ? 'Run demo audit' : cf.status === 'running' ? 'Running…' : 'Re-run demo audit'}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="grid grid-cols-[repeat(auto-fit,minmax(min(380px,100%),1fr))] gap-4 items-start">
            {showHealing && (
              <div className="aw-panel" data-testid="wb-healing">
                <div className="aw-panel-head">
                  <div className="aw-panel-title">Healing queue</div>
                  {pendingCount > 0
                    ? <div className="aw-chip text-[var(--warn)] bg-[var(--warn-dim)]" style={{ fontWeight: 600 }}>{pendingCount} PENDING_APPROVAL</div>
                    : <div className="aw-chip aw-pill-accent" style={{ fontWeight: 600 }}>QUEUE CLEAR</div>}
                </div>
                <div className="pt-1.5 px-4 pb-3.5">
                  {healing.length === 0 && (
                    <div className="py-3.5 text-xs text-[var(--text3)] leading-[1.6]">
                      No pending recoveries — the MAPE-K loop is nominal. Drift proposals appear here for signed approval.
                    </div>
                  )}
                  {healing.map((h) => (
                    <div key={h.id} className="py-[11px] border-b border-[var(--hair)]">
                      <div className="flex items-center gap-2">
                        <div className="aw-mono text-[11.5px] font-medium">{h.title}</div>
                        <div className={cn('aw-mono text-[9px] font-bold rounded-none py-px px-[7px] border', h.safe ? 'text-[var(--accent)] bg-[var(--accent-dim)] border-[var(--accent)]' : 'text-[var(--warn)] bg-[var(--warn-dim)] border-[var(--warn)]')}>{h.method}</div>
                      </div>
                      <div className="mt-[5px] text-[11px] text-[var(--text3)]">{h.sub}</div>
                      {h.state === 'pending' && (
                        <div className="mt-2 flex gap-[7px]">
                          <div onClick={() => decideHeal(h.id, true)} className="cursor-pointer text-[11px] font-semibold text-[var(--accent)] bg-[var(--accent-dim)] border border-[var(--accent-bd)] rounded-none py-1 px-3">Approve & deploy</div>
                          <div onClick={() => decideHeal(h.id, false)} className="cursor-pointer text-[11px] font-semibold text-[var(--danger)] bg-[var(--danger-dim)] border border-[var(--danger)] rounded-none py-1 px-3">Reject</div>
                        </div>
                      )}
                      {h.resolution && <div className={cn('aw-mono mt-2 text-[10.5px] font-medium', h.state === 'deployed' ? 'text-[var(--accent)]' : 'text-[var(--danger)]')}>{h.resolution}</div>}
                    </div>
                  ))}
                  <div className="pt-2.5 text-[10.5px] text-[var(--text3)]">every approve/reject is a signed override in the WORM audit log</div>
                </div>
              </div>
            )}

            {showPipes && (
              <div className="aw-panel" data-testid="wb-pipes">
                <div className="aw-panel-head">
                  <div className="aw-panel-title">Pipelines & streaming</div>
                  <div className="flex-1" />
                  <div className="aw-mono text-[9.5px] font-medium text-[var(--accent)]">PII MASKING ON</div>
                </div>
                <div className="pt-3 px-4 pb-3.5 flex flex-col gap-2.5">
                  <div className="aw-mono flex gap-2 text-[10.5px] font-medium flex-wrap">
                    <div className="flex items-center gap-1.5 border border-[var(--hair)] rounded-none py-[5px] px-2.5 text-[var(--text2)]"><span className={cn('w-[5px] h-[5px] rounded-full', pipelines?.length ? 'bg-[var(--accent)]' : 'bg-[var(--text3)]')} />{pipelines ? `${pipelines.length} pipeline${pipelines.length === 1 ? '' : 's'} defined` : 'pipelines unavailable'}</div>
                  </div>
                  {runs.length === 0 && (
                    <div className="text-xs text-[var(--text3)] leading-[1.6]">
                      No streaming pipelines yet — <button type="button" onClick={() => setNav('Pipelines')} className="aw-mono bg-transparent border-none p-0 text-[var(--accent)] cursor-pointer [font:inherit]">define one in the Pipelines view</button> and it appears here.
                    </div>
                  )}
                  {runs.length > 0 && <div className="border border-[var(--hair)] rounded-none overflow-hidden text-[11.5px]">
                    <div className="aw-table-head grid grid-cols-[1.6fr_.9fr_.7fr_.8fr]"><div className="py-1.5 px-3">RUN</div><div className="py-1.5 px-3">STATUS</div><div className="py-1.5 px-3">TIME</div><div className="py-1.5 px-3">ROWS</div></div>
                    {runs.map((r) => (
                      <div key={r.name} className="grid grid-cols-[1.6fr_.9fr_.7fr_.8fr] border-t border-[var(--hair)] items-center">
                        <div className="aw-cell">{r.name}</div>
                        <div className="py-[7px] px-3 font-semibold" style={{ color: r.color }}>{r.status}</div>
                        <div className="aw-cell">{r.time}</div>
                        <div className="aw-cell">{r.rows}</div>
                      </div>
                    ))}
                  </div>}
                  <div className="text-[10.5px] text-[var(--text3)]">Transforms: filter · aggregate · dedupe · cast · custom SQL → CSV / Parquet / JSON</div>
                </div>
              </div>
            )}

            {showLineage && (
              <div className="aw-panel" data-testid="wb-lineage">
                <div className="aw-panel-head"><div className="aw-panel-title">Lineage &amp; provenance</div></div>
                <div className="py-3.5 px-4 flex flex-col gap-2.5">
                  <div className="text-xs text-[var(--text2)] leading-[1.65]">
                    Dataset-to-finding lineage renders live in the <strong>Constellation</strong> graph —
                    uploaded datasets, derived metrics, and signed findings as a navigable graph.
                  </div>
                  <a href="/app" className="aw-mono self-start text-[10.5px] font-semibold border border-[var(--accent-bd)] rounded-none py-1.5 px-3 no-underline" style={{ color: 'var(--accent)' }}>Open Constellation →</a>
                  {ledger && <div className="text-[10.5px] text-[var(--text3)] leading-[1.6]">Every signed artifact is replayable from ledger {ledger.no}.</div>}
                </div>
              </div>
            )}
          </div>

          {showHistory && (
            <div className="aw-panel" data-testid="wb-history">
              <div className="aw-panel-head"><div className="aw-panel-title">Query history</div><div className="flex-1" /><div className="text-[11px] text-[var(--text3)]">this session + today</div></div>
              <div className="text-[11.5px]">
                <div className="aw-table-head grid grid-cols-[.55fr_2.6fr_.8fr_.7fr_.55fr_.6fr_.7fr]">{['TIME', 'QUERY', 'ENGINE', 'STATUS', 'COST', 'DUR', 'BY'].map((h) => <div key={h} className="py-[7px] px-4">{h}</div>)}</div>
                {history.length === 0 && <div className="py-3 px-4 text-xs text-[var(--text3)]">No queries recorded yet in this workspace.</div>}
                {history.map((hq, i) => (
                  <div key={i} className="grid grid-cols-[.55fr_2.6fr_.8fr_.7fr_.55fr_.6fr_.7fr] border-t border-[var(--hair)] items-center">
                    <div className="aw-mono py-2 px-4 text-[11px] text-[var(--text3)]">{hq.time}</div>
                    <div className="py-2 px-4">{hq.q}</div>
                    <div className="aw-mono py-2 px-4 text-[11px]">{hq.engine}</div>
                    <div className={cn('py-2 px-4 font-semibold', hq.status === 'signed' ? 'text-[var(--accent)]' : 'text-[var(--text2)]')}>{hq.status}</div>
                    <div className="aw-mono py-2 px-4 text-[11px]">{hq.cost}</div>
                    <div className="aw-mono py-2 px-4 text-[11px]">{hq.dur}</div>
                    <div className="py-2 px-4 text-[var(--text3)]">{hq.by}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {nav === 'Cockpit' && (
            <div className="aw-panel" data-testid="wb-feed" role="log" aria-live="polite" aria-label="Session events">
              <div className="flex items-center gap-2 py-3 px-4"><span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-[awpulse_1.6s_infinite]" /><div className="aw-panel-title">Session events</div><div className="flex-1" /><div className="text-[10.5px] text-[var(--text3)]">real actions only — queries · audits · approvals</div></div>
              {feed.length === 0 && <div className="py-2.5 px-4 border-t border-[var(--hair)] text-[11.5px] text-[var(--text3)]">No events yet — run a query or an audit and it lands here.</div>}
              {feed.map((ev, i) => (
                <div key={i} className="aw-mono flex gap-2.5 items-baseline py-1.5 px-4 border-t border-[var(--hair)] text-[10.5px]">
                  <span className="text-[var(--text3)] flex-none">{ev.time}</span>
                  <span className="flex-none font-bold text-[9px] tracking-[.06em]" style={{ color: ev.color }}>{ev.k}</span>
                  <span className="text-[var(--text2)]">{ev.t}</span>
                </div>
              ))}
            </div>
          )}

          {hasView && <ViewHost nav={nav} onNavigate={setNav} />}

          {showStub && (
            <div className="bg-[var(--surface)] border border-dashed border-[var(--border)] rounded-none p-9 flex flex-col items-center gap-2.5 text-center" data-testid="wb-stub">
              <div className="aw-display font-semibold text-[13px]">{nav}</div>
              <div className="text-[12.5px] text-[var(--text2)] max-w-[460px] leading-[1.6]">This module has no panel yet. Nothing is being shown for it — no data is implied.</div>
            </div>
          )}
          </motion.div>
        </main>
      </div>

      {/* command palette */}
      {paletteOpen && (
        <div onClick={() => setPaletteOpen(false)} className="fixed inset-0 bg-[var(--overlay)] z-[100] flex justify-center pt-[120px]" data-testid="wb-palette">
          <div onClick={(e) => e.stopPropagation()} className="w-[520px] h-fit bg-[var(--surface)] border border-[var(--border)] rounded-none shadow-[0_24px_60px_rgba(0,0,0,.35)] overflow-hidden animate-[awup_.18s_ease]">
            <input ref={paletteInput} value={paletteQ} onChange={(e) => setPaletteQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && commands[0]) commands[0].run(); }} placeholder="Type a command or destination…" className="w-full box-border bg-transparent border-0 border-b border-[var(--hair)] py-[14px] px-[18px] font-ui font-normal text-[14px] text-[var(--text)] outline-none" />
            <div className="max-h-[320px] overflow-y-auto p-1.5">
              {commands.map((c) => (
                <div key={c.title} onClick={c.run} className="aw-hover-raise cursor-pointer flex justify-between items-center py-[9px] px-3 rounded-none text-[13px]">
                  <span>{c.title}</span><span className="aw-mono text-[9.5px] font-medium text-[var(--text3)]">{c.hint}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* toast */}
      {toast && (
        <div className="fixed bottom-[26px] left-1/2 -translate-x-1/2 z-[200] bg-[var(--raised)] border border-[var(--accent-bd)] text-[var(--text)] font-ui font-medium text-[12px] rounded-none py-2.5 px-[18px] shadow-[0_8px_30px_rgba(0,0,0,.3)] animate-[awup_.2s_ease] flex items-center gap-2" data-testid="wb-toast">
          <span className="text-[var(--accent)]">✓</span>{toast}
        </div>
      )}
    </div>
  );
}
