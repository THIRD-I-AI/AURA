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
    return `${CF}/artifacts/${hash}/report.pdf`;
  },
};
