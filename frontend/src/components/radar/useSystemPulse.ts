/* useSystemPulse — reactive live model for the System Radar.
   Standalone consumers (e.g. the terminal deck) can drop this in and get a
   self-refreshing SystemRadarModel. The workbench already owns this state, so
   it builds the model inline instead of using this hook — but the fetch /
   degrade discipline is identical and lives here for reuse.

   Guarantees the standing AURA principles:
     • reactive   — polls on an interval + on tab refocus, so new pipelines /
                    changed health / fresh drift reflect on their own
     • resilient  — every request is abortable; every failure degrades to an
                    honest state (gatewayUp=false, empty sources) and never
                    throws into the render tree
     • honest     — unknown health is null (awaiting), never a fake green */
import { useEffect, useRef, useState } from 'react';
import { ROOT_BASE_URL, healingService, streamingService } from '../../services/api';
import type { SystemRadarModel, RadarSource, Severity } from './types';

const EMPTY: SystemRadarModel = { core: 'AURA', gatewayUp: null, services: [], sources: [] };

function severityFrom(kl: number | null, validationPassed: boolean | null): Severity {
  // A pending recovery that failed validation is the most urgent.
  if (validationPassed === false) return 'critical';
  if (kl == null) return 'medium'; // drift detected, magnitude unknown → mid
  if (kl >= 0.5) return 'critical';
  if (kl >= 0.2) return 'high';
  if (kl >= 0.05) return 'medium';
  return 'low';
}

export function useSystemPulse(intervalMs = 10000, enabled = true): SystemRadarModel {
  const [model, setModel] = useState<SystemRadarModel>(EMPTY);
  const inFlight = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;

    const pulse = async () => {
      if (!alive || inFlight.current) return;
      inFlight.current = true;
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), 8000);
      try {
        let gatewayUp: boolean | null = null;
        let services: SystemRadarModel['services'] = [];
        try {
          const r = await fetch(`${ROOT_BASE_URL}/health`, { signal: ac.signal });
          gatewayUp = r.ok;
          const j = r.ok ? await r.json() : null;
          if (j) {
            const src = j.services ?? j.components ?? j.checks;
            const entries = src && typeof src === 'object' ? Object.entries(src) : [];
            services = entries.map(([name, v]) => ({
              id: name,
              label: name.replace(/[_-]?service$/i, '').slice(0, 12),
              up: /health|ok|up|pass/i.test(String((v as { status?: string })?.status ?? v)),
            }));
          }
        } catch (e) {
          if ((e as Error)?.name !== 'AbortError') gatewayUp = false;
        }

        // Sources = streaming pipelines, enriched with drift severity from
        // any pending recovery that references them.
        const sources: RadarSource[] = [];
        const recovering = new Map<string, Severity>();
        try {
          const pend = await healingService.pending();
          for (const p of pend) {
            const sid = p.source_id || p.drift_event_id;
            if (sid) recovering.set(sid, severityFrom(p.post_kl_divergence, p.validation_passed));
          }
        } catch { /* honest: no recovery info this cycle */ }

        try {
          const r = await streamingService.list();
          for (const p of (r.pipelines ?? []).slice(0, 10)) {
            const name = (p as { name?: string; pipeline_id?: string }).name
              ?? (p as { pipeline_id?: string }).pipeline_id ?? 'pipeline';
            const sev = recovering.get(name);
            sources.push({
              id: name,
              label: String(name).slice(0, 12),
              severity: sev ?? 'none',
              recovering: recovering.has(name),
            });
          }
        } catch { /* honest: no pipelines this cycle */ }

        // Any recovering source not already listed as a pipeline still shows.
        for (const [sid, sev] of recovering) {
          if (!sources.some((s) => s.id === sid)) {
            sources.push({ id: sid, label: sid.slice(0, 12), severity: sev, recovering: true });
          }
        }

        if (alive) setModel({ core: 'AURA', gatewayUp, services, sources });
      } finally {
        clearTimeout(timer);
        inFlight.current = false;
      }
    };

    pulse();
    const id = setInterval(pulse, intervalMs);
    const onVis = () => { if (document.visibilityState === 'visible') pulse(); };
    document.addEventListener('visibilitychange', onVis);
    return () => { alive = false; clearInterval(id); document.removeEventListener('visibilitychange', onVis); };
  }, [intervalMs, enabled]);

  return model;
}
