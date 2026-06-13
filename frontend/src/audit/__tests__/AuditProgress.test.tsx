import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

import { AuditProgress } from '../AuditProgress';
import * as polling from '../useJobPolling';

function renderAt(jobId: string) {
  return render(
    <MemoryRouter initialEntries={[`/audit/${jobId}`]}>
      <Routes><Route path="/audit/:jobId" element={<AuditProgress />} /></Routes>
    </MemoryRouter>,
  );
}

describe('AuditProgress', () => {
  afterEach(() => { vi.restoreAllMocks(); navigate.mockClear(); });

  it('renders an estimator row per estimate in the running snapshot', () => {
    vi.spyOn(polling, 'useJobPolling').mockReturnValue({
      snapshot: { job_id: 'j', state: 'running', artifact: { audit_record_hash: '', refutations: [], signature_status: '', signing_key_source: '', estimates: [
        { method: 'linear_regression', point: 0.12, ci_lower: 0.05, ci_upper: 0.2 },
        { method: 'double_ml' },
      ] } },
      error: null,
    });
    renderAt('j');
    expect(screen.getByTestId('estimator-linear_regression')).toBeInTheDocument();
    expect(screen.getByTestId('estimator-double_ml')).toBeInTheDocument();
  });

  it('coerces string-typed numbers from the replay path when formatting an estimate', () => {
    // GET /artifacts/{hash} (replay) returns numbers as STRINGS (S31b §5.1).
    vi.spyOn(polling, 'useJobPolling').mockReturnValue({
      snapshot: { job_id: 'j', state: 'running', artifact: { audit_record_hash: '', refutations: [], signature_status: '', signing_key_source: '', estimates: [
        { method: 'tmle', point: '-0.393147', ci_lower: '-0.5', ci_upper: '-0.28' },
      ] } },
      error: null,
    });
    renderAt('j');
    // Coerced + fixed to 3dp, not the raw string.
    expect(screen.getByTestId('estimator-tmle')).toHaveTextContent('-0.393 [-0.50, -0.28]');
  });

  it('navigates to the certificate when the job succeeds', async () => {
    vi.spyOn(polling, 'useJobPolling').mockReturnValue({
      snapshot: { job_id: 'j', state: 'succeeded', artifact: { audit_record_hash: 'HASH', refutations: [], signature_status: 'ok', signing_key_source: 'persisted_file', estimates: [] } },
      error: null,
    });
    renderAt('j');
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/certificate/HASH'));
  });

  it('shows a failure panel on failed state', () => {
    vi.spyOn(polling, 'useJobPolling').mockReturnValue({ snapshot: { job_id: 'j', state: 'failed', error: 'engine error' }, error: null });
    renderAt('j');
    expect(screen.getByTestId('audit-failed')).toHaveTextContent('engine error');
  });

  it('shows a stage timeline driven by job state', () => {
    vi.spyOn(polling, 'useJobPolling').mockReturnValue({
      snapshot: { job_id: 'j', state: 'running', artifact: { audit_record_hash: '', refutations: [], signature_status: '', signing_key_source: '', estimates: [] } },
      error: null,
    });
    renderAt('j');
    expect(screen.getByTestId('aud-stages')).toBeInTheDocument();
  });
});
