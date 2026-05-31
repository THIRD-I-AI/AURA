import { API_BASE_URL } from '../services/api';
import type { Scenario, JobSnapshot, DemoSubmitResult, VerifyResult, Artifact } from './types';

const CF = `${API_BASE_URL}/counterfactual`;

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

export const auditApi = {
  async listScenarios(): Promise<Scenario[]> {
    const body = await getJson<{ scenarios: Scenario[] }>(`${CF}/demo/scenarios`);
    return body.scenarios;
  },

  async runScenario(scenarioId: string): Promise<DemoSubmitResult> {
    const resp = await fetch(`${CF}/demo/${scenarioId}`, { method: 'POST' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json() as Promise<DemoSubmitResult>;
  },

  getJob(jobId: string): Promise<JobSnapshot> {
    return getJson<JobSnapshot>(`${CF}/jobs/${jobId}`);
  },

  getArtifact(hash: string): Promise<Artifact> {
    return getJson<Artifact>(`${CF}/artifacts/${hash}`);
  },

  verify(hash: string): Promise<VerifyResult> {
    return getJson<VerifyResult>(`${CF}/artifacts/${hash}/verify`);
  },

  pdfUrl(hash: string): string {
    const url = `${CF}/artifacts/${hash}/report.pdf`;
    // CF derives from a localStorage-set API base; guarantee the value handed to
    // an anchor href is a well-formed http(s) URL so a javascript:/data: base
    // can't reach the DOM (CodeQL js/xss-through-dom). Bad input → safe '#'.
    try {
      const u = new URL(url, window.location.origin);
      if (u.protocol === 'http:' || u.protocol === 'https:') return u.href;
    } catch { /* malformed URL → fall through */ }
    return '#';
  },

  async submitCustomAudit(query: Record<string, unknown>): Promise<{ job_id: string }> {
    const resp = await fetch(`${CF}/jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(query),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json() as Promise<{ job_id: string }>;
  },
};
