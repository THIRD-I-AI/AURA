import { API_BASE_URL } from '../services/api';
import type { Scenario, JobSnapshot, DemoSubmitResult, VerifyResult, Artifact, DataAuditRequest } from './types';

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

  async verify(hash: string): Promise<VerifyResult> {
    // Two record families share /verify/:hash — counterfactual artifacts and
    // financial-audit records. The artifact store answers "unsigned" for a
    // hash it never signed, so fall through to the financial verifier before
    // declaring an artifact unsigned (live-data bug: signed forensic audits
    // rendered as NOT verified).
    const art = await getJson<VerifyResult>(`${CF}/artifacts/${hash}/verify`).catch(() => null);
    if (art && art.signature_status === 'signed') return art;
    const fin = await getJson<VerifyResult>(`${CF}/audit/financial/verify/${hash}`).catch(() => null);
    if (fin && fin.signature_status === 'signed') return fin;
    if (art) return art;
    if (fin) return fin;
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

  async uploadDataset(file: File): Promise<{ filename: string }> {
    const form = new FormData();
    form.append('file', file);
    const resp = await fetch(`${API_BASE_URL}/upload`, { method: 'POST', body: form });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    const body = (await resp.json()) as { filename: string };
    return { filename: body.filename };
  },

  async runDataAudit(req: DataAuditRequest): Promise<{ job_id: string }> {
    const resp = await fetch(`${CF}/audit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json() as Promise<{ job_id: string }>;
  },
};
