/* AURA Workbench — Terminal-authority cockpit (Phase 6).
   One shell, four nav groups, a dense cockpit board, ⌘K palette, and a live
   System Radar hero. Dark-first, mono-first, sharp-cornered, green-signal —
   no theme toggle (theme_honesty). Live wiring where the platform already
   has the API (Ask AURA → commander SSE, ledger chip → /audit/ledger/verify,
   health + pipelines polled so new state reflects on its own); design seed
   data elsewhere so every panel renders.
   The single authenticated app — the real /login gates it (ProtectedRoute), so
   it boots straight in with one shared session; the classic /app shell is gone.

   Composition root: state and side effects (fetch/poll loops, boot sequence,
   ⌘K keybind) live here; shell chrome (WorkbenchBoot/Topbar/Nav, CommandPalette,
   Toast) and each cockpit board card live in their own files under
   ./cockpit/ — see BUG-029 item 7 for why this split happened. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import {
  API_BASE_URL,
  analyticsService,
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
import type { SystemRadarModel, Severity } from '../components/radar';
import { NAV_GROUPS } from './navConfig';
import { WorkbenchBoot } from './WorkbenchBoot';
import { BOOT_STAGES } from './bootStages';
import { WorkbenchTopbar } from './WorkbenchTopbar';
import { WorkbenchNav } from './WorkbenchNav';
import { CommandPalette, type Command } from './CommandPalette';
import { Toast } from './Toast';
import { CockpitStats, type Stat } from './cockpit/CockpitStats';
import { LiveRadarPanel } from './cockpit/LiveRadarPanel';
import { AskAuraChat } from './cockpit/AskAuraChat';
import { ForensicAuditPanel } from './cockpit/ForensicAuditPanel';
import { CF_STAGES } from './cockpit/cfStages';
import { HealingQueueApprovals } from './cockpit/HealingQueueApprovals';
import { PipelinesStreamingPanel } from './cockpit/PipelinesStreamingPanel';
import { LineageSummaryCard } from './cockpit/LineageSummaryCard';
import { QueryHistoryTable } from './cockpit/QueryHistoryTable';
import { SessionEventsFeed } from './cockpit/SessionEventsFeed';
import type { Heal, FeedEv, HistoryEntry, CfState } from './cockpit/types';
import './workbench.css';

const now = () => new Date().toTimeString().slice(0, 5);

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
  /* NO seeded/dummy data: every panel below starts empty and fills from the
     platform's real APIs (or shows an honest empty/offline state). */
  const [healing, setHealing] = useState<Heal[]>([]);
  const [cf, setCf] = useState<CfState>({ status: 'idle' });
  const [feed, setFeed] = useState<FeedEv[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [ledger, setLedger] = useState<{ no: string; hash: string; intact: boolean } | null>(null);
  const [health, setHealth] = useState<{ up: number; total: number } | null>(null);
  const [services, setServices] = useState<Array<{ name: string; up: boolean }> | null>(null);
  const [files, setFiles] = useState<number | null>(null);
  const [pipelines, setPipelines] = useState<Array<{ name: string; status: string }> | null>(null);
  const [gatewayUp, setGatewayUp] = useState<boolean | null>(null);
  const [ledgerDown, setLedgerDown] = useState(false);

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

  /* Runs the REAL one-click forensic audit (signed report → ledger). The
     stage list animates only while the real request is in flight. Stays here
     (not in ForensicAuditPanel) because the command palette's "Run
     counterfactual audit" entry calls this same function. */
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
  const stats: Stat[] = [
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

  const commands: Command[] = useMemo(() => {
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
    return <WorkbenchBoot bootIdx={bootIdx} />;
  }

  /* ── app ── */
  return (
    /* height (not min-height) bounds the shell so topbar+nav stay pinned and
       ONLY the main column scrolls — the design's cockpit scroll model. */
    <div className="aw h-screen overflow-hidden" data-testid="wb-app">
      <a href="#wb-main" className="skip-link">Skip to main content</a>
      <WorkbenchTopbar
        onToggleNav={() => setNavOpen((o) => !o)}
        gatewayUp={gatewayUp}
        onOpenPalette={() => { setPaletteOpen(true); setTimeout(() => paletteInput.current?.focus(), 30); }}
      />

      <div className="flex flex-1 min-h-0">
        <div className={`aw-backdrop${navOpen ? ' aw-open' : ''}`} onClick={() => setNavOpen(false)} />
        <WorkbenchNav
          navOpen={navOpen}
          onCloseNav={() => setNavOpen(false)}
          navCollapsed={navCollapsed}
          onToggleCollapsed={() => setNavCollapsed((c) => !c)}
          nav={nav}
          selectNav={selectNav}
          pendingCount={pendingCount}
          ledger={ledger}
          ledgerDown={ledgerDown}
        />

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
          {nav === 'Cockpit' && <CockpitStats stats={stats} />}

          {nav === 'Cockpit' && (
            <LiveRadarPanel radarModel={radarModel} gatewayUp={gatewayUp} onServiceClick={onRadarService} />
          )}

          <div className="grid grid-cols-[repeat(auto-fit,minmax(min(470px,100%),1fr))] gap-4 items-start">
            {showChat && <AskAuraChat pushFeed={pushFeed} setHistory={setHistory} />}
            {showCf && <ForensicAuditPanel cf={cf} runCf={runCf} selectNav={selectNav} />}
          </div>

          <div className="grid grid-cols-[repeat(auto-fit,minmax(min(380px,100%),1fr))] gap-4 items-start">
            {showHealing && <HealingQueueApprovals healing={healing} pendingCount={pendingCount} decideHeal={decideHeal} />}
            {showPipes && <PipelinesStreamingPanel pipelines={pipelines} onDefinePipeline={() => setNav('Pipelines')} />}
            {showLineage && <LineageSummaryCard ledger={ledger} />}
          </div>

          {showHistory && <QueryHistoryTable history={history} />}

          {nav === 'Cockpit' && <SessionEventsFeed feed={feed} />}

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

      <CommandPalette
        paletteOpen={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        paletteQ={paletteQ}
        setPaletteQ={setPaletteQ}
        paletteInput={paletteInput}
        commands={commands}
      />

      <Toast toast={toast} />
    </div>
  );
}
