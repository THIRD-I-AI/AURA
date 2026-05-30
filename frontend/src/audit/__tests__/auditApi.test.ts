import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { auditApi } from '../auditApi';
import { API_BASE_URL } from '../../services/api';

function mockJson(body: unknown, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) } as Response);
}

describe('auditApi', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('listScenarios GETs the demo scenarios endpoint and unwraps the array', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ scenarios: [{ id: 'fair_lending', title: 'T', vertical: 'compliance', description: 'd' }] }));
    const out = await auditApi.listScenarios();
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/demo/scenarios`);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe('fair_lending');
  });

  it('runScenario POSTs to the demo scenario endpoint', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ job_id: 'ca_1', scenario_id: 'fair_lending', degraded: false }));
    const out = await auditApi.runScenario('fair_lending');
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/demo/fair_lending`, expect.objectContaining({ method: 'POST' }));
    expect(out.job_id).toBe('ca_1');
  });

  it('getJob GETs the job snapshot', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ job_id: 'ca_1', state: 'running' }));
    const out = await auditApi.getJob('ca_1');
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/jobs/ca_1`);
    expect(out.state).toBe('running');
  });

  it('verify GETs the artifact verify endpoint', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ record_hash: 'h', verified: true, signature_status: 'ok', signing_key_source: 'persisted_file' }));
    const out = await auditApi.verify('h');
    expect(fetch).toHaveBeenCalledWith(`${API_BASE_URL}/counterfactual/artifacts/h/verify`);
    expect(out.verified).toBe(true);
  });

  it('throws on non-ok responses', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(mockJson({ detail: 'boom' }, false, 500));
    await expect(auditApi.getJob('ca_1')).rejects.toThrow(/500/);
  });

  it('pdfUrl and verifyPath build correct hrefs without fetching', () => {
    expect(auditApi.pdfUrl('h')).toBe(`${API_BASE_URL}/counterfactual/artifacts/h/report.pdf`);
  });
});
