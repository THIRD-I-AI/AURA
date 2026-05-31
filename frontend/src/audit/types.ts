export interface Scenario {
  id: string;
  title: string;
  vertical: string;
  description: string;
}

export interface Estimate {
  method: string;
  // Numbers are JSON floats on the live/demo job path but STRINGS on the
  // byte-identical replay path (GET /artifacts/{hash}) — always coerce with
  // Number() before arithmetic/formatting. (S31b spec §5.1.)
  point?: number | string;
  ci_lower?: number | string;
  ci_upper?: number | string;
  n_samples?: number;
  error?: string | null;
}

export interface Artifact {
  audit_record_hash: string;
  estimates: Estimate[];
  refutations: unknown[];
  signature_status: string;
  signing_key_source: string;
  rendered?: unknown;
  // S31b fail-safe: a live run that errored served the last-good sealed
  // artifact instead. Still a valid certificate, but worth noting to the user.
  degraded?: boolean;
}

export type JobState = 'queued' | 'running' | 'succeeded' | 'failed';

export interface JobSnapshot {
  job_id: string;
  state: JobState;
  artifact?: Artifact;
  error?: string;
}

export interface DemoSubmitResult {
  job_id: string;
  scenario_id: string;
  degraded: boolean;
  // S31b pre-warms scenarios at startup; cached=true means an instant
  // pre-sealed artifact was returned rather than a fresh live run.
  cached?: boolean;
}

export interface VerifyResult {
  record_hash: string;
  verified: boolean;
  signature_status: string;
  signing_key_source: string;
  reason?: string;
}
