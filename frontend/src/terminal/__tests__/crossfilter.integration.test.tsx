import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// End-to-end cross-filter proof through REAL components (no mock of useCockpit):
// selecting a dataset in the Datasets panel filters the Findings panel, both
// composed under one real CockpitProvider. Only the data services are mocked.
const getUploadedFiles = vi.fn();
const ensureAuditorToken = vi.fn().mockResolvedValue(undefined);
const runAudit = vi.fn();
vi.mock('../../services/api', () => ({
  uploadService: { getUploadedFiles: () => getUploadedFiles() },
  financialAuditService: { ensureAuditorToken: () => ensureAuditorToken(), runAudit: () => runAudit() },
}));
vi.mock('../../audit/sampleAuditBatch', () => ({ SAMPLE_AUDIT_BATCH: { tenant_id: 'demo' } }));

import { CockpitProvider } from '../CockpitProvider';
import DatasetsPanel from '../panels/DatasetsPanel';
import FindingsPanel from '../panels/FindingsPanel';

// Panels ignore their dockview props; a cast spread keeps `tsc -b` (the build) happy.
const panelProps = { api: {}, params: {}, containerApi: {} } as any;

describe('cross-filter integration', () => {
  it('selecting a dataset in Datasets filters the Findings panel', async () => {
    getUploadedFiles.mockResolvedValue([{ filename: 'sales.csv', size: 1, modified: 'now' }]);
    runAudit.mockResolvedValue({
      record_hash: 'abc', signature_status: 'signed', n_findings: 2,
      findings: [
        { finding_id: 'f1', pcaob_standard: 'AS-2401', risk_level: 'High', description: 'anomaly in sales.csv', evidence_payload: {}, requires_human_review: true },
        { finding_id: 'f2', pcaob_standard: 'AS-2201', risk_level: 'Low', description: 'orders mismatch', evidence_payload: {}, requires_human_review: false },
      ],
    });

    render(
      <CockpitProvider>
        <DatasetsPanel {...panelProps} />
        <FindingsPanel {...panelProps} />
      </CockpitProvider>,
    );

    // load findings (the panel does not auto-run an audit)
    fireEvent.click(screen.getByTestId('findings-run'));
    await waitFor(() => expect(screen.getByText(/orders mismatch/)).toBeInTheDocument());

    // cross-filter: select a dataset → Findings narrows to matching rows
    fireEvent.click(screen.getByTestId('dataset-row-sales.csv'));
    await waitFor(() => expect(screen.queryByText(/orders mismatch/)).not.toBeInTheDocument());
    expect(screen.getByText(/anomaly in sales.csv/)).toBeInTheDocument();
  });
});
