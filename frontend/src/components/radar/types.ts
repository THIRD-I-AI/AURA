/* Live System Radar — honest data model.
   The radar maps the *real* AURA topology, not geography:
     • a central hub  = the AURA gateway / core
     • service nodes  = the backend services reported by /health
     • source rings   = streaming pipelines / data sources; each pulses when
                        its drift severity rises
     • recovery arcs  = an animated arc from a source to the core when a shim
                        is deploying (a real recovery in flight)
   Every field is derived from a live endpoint. When we have no data we say so
   with an honest glyph state — we never fabricate a healthy/verified signal. */

export type Severity = 'none' | 'low' | 'medium' | 'high' | 'critical';

/** A backend service reported by /health. */
export interface RadarService {
  id: string;
  /** Short display label. */
  label: string;
  /** true = health check passing; false = failing; null = unknown/awaiting. */
  up: boolean | null;
}

/** A streaming pipeline / data source orbiting the rim. */
export interface RadarSource {
  id: string;
  label: string;
  /** Drift severity — drives ring colour + pulse. 'none' = idle. */
  severity: Severity;
  /** true while a recovery shim is deploying for this source. */
  recovering?: boolean;
}

export interface SystemRadarModel {
  /** Central hub label, e.g. "AURA". */
  core: string;
  /** true = gateway reachable, false = offline, null = still checking. */
  gatewayUp: boolean | null;
  services: RadarService[];
  sources: RadarSource[];
}

/** Severity → token colour var. Kept in one place so terminal + workbench
    read identically. */
export const SEVERITY_COLOR: Record<Severity, string> = {
  none: 'var(--accent, #22c55e)',
  low: 'var(--accent, #22c55e)',
  medium: 'var(--warn, #f59e0b)',
  high: 'var(--warn, #f59e0b)',
  critical: 'var(--danger, #ef4444)',
};

/** Severity ranked for sizing/urgency. */
export const SEVERITY_RANK: Record<Severity, number> = {
  none: 0, low: 1, medium: 2, high: 3, critical: 4,
};
