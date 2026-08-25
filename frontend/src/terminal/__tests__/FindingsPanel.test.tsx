import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const ensureAuditorToken = vi.fn().mockResolvedValue(undefined);
const runAudit = vi.fn();
vi.mock('../../services/api', () => ({
  financialAuditService: { ensureAuditorToken: () => ensureAuditorToken(), runAudit: () => runAudit() },
}));
vi.mock('../../audit/sampleAuditBatch', () => ({ SAMPLE_AUDIT_BATCH: { tenant_id: 'demo' } }));
let active: string | null = null;
vi.mock('../CockpitProvider', () => ({ useCockpit: () => ({ activeDataset: active, setActiveDataset: () => {} }) }));

import FindingsPanel from '../panels/FindingsPanel';

const REPORT = {
  record_hash: 'abc', signature_status: 'signed', n_findings: 2,
  findings: [
    { finding_id: 'f1', pcaob_standard: 'AS-2401', risk_level: 'High', description: 'anomaly in sales.csv', evidence_payload: {}, requires_human_review: true },
    { finding_id: 'f2', pcaob_standard: 'AS-2201', risk_level: 'Low', description: 'orders mismatch', evidence_payload: {}, requires_human_review: false },
  ],
};
const props = { api: {}, params: {}, containerApi: {}, group: {} } as any;

describe('FindingsPanel', () => {
  it('runs a sample audit, lists findings, and filters by active dataset', async () => {
    active = null;
    runAudit.mockResolvedValue(REPORT);
    const { rerender } = render(<FindingsPanel {...props} />);
    fireEvent.click(screen.getByTestId('findings-run'));
    await waitFor(() => expect(screen.getByText(/anomaly in sales.csv/)).toBeInTheDocument());
    expect(screen.getByText(/orders mismatch/)).toBeInTheDocument();

    active = 'sales.csv';
    rerender(<FindingsPanel {...props} />);
    await waitFor(() => expect(screen.queryByText(/orders mismatch/)).not.toBeInTheDocument());
    expect(screen.getByText(/anomaly in sales.csv/)).toBeInTheDocument();
  });

  it('filters by free text across standard and description, composing with the dataset filter', async () => {
    active = null;
    runAudit.mockResolvedValue(REPORT);
    render(<FindingsPanel {...props} />);
    fireEvent.click(screen.getByTestId('findings-run'));
    await waitFor(() => expect(screen.getByText(/anomaly in sales.csv/)).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('findings-filter'), { target: { value: 'AS-2201' } });
    expect(screen.queryByText(/anomaly in sales.csv/)).not.toBeInTheDocument();
    expect(screen.getByText(/orders mismatch/)).toBeInTheDocument();
  });

  it('sorts by risk severity, not alphabetically', async () => {
    active = null;
    runAudit.mockResolvedValue(REPORT);
    render(<FindingsPanel {...props} />);
    fireEvent.click(screen.getByTestId('findings-run'));
    await waitFor(() => expect(screen.getByText(/anomaly in sales.csv/)).toBeInTheDocument());

    fireEvent.click(screen.getByText('Risk'));
    let rows = screen.getAllByRole('row').slice(1); // drop header row
    expect(rows[0]).toHaveTextContent('Low');
    expect(rows[1]).toHaveTextContent('High');

    fireEvent.click(screen.getByText('Risk'));
    rows = screen.getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('High');
    expect(rows[1]).toHaveTextContent('Low');
  });
});
