export interface Scenario {
  id: string;
  title: string;
  vertical: string;
  description: string;
}

export interface Estimate {
  method: string;
  point_estimate?: number;
  ci_low?: number;
  ci_high?: number;
  error?: string;
}

export interface Artifact {
  audit_record_hash: string;
  estimates: Estimate[];
  refutations: unknown[];
  signature_status: string;
  signing_key_source: string;
  rendered?: unknown;
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
}

export interface VerifyResult {
  record_hash: string;
  verified: boolean;
  signature_status: string;
  signing_key_source: string;
  reason?: string;
}
