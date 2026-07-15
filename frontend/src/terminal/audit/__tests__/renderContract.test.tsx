import type React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const ensureAuditorToken = vi.fn().mockResolvedValue(undefined);
const runAudit = vi.fn();
const verify = vi.fn();
const decide = vi.fn();

vi.mock('../../../services/api', () => ({
  financialAuditService: {
    ensureAuditorToken: () => ensureAuditorToken(),
    runAudit: () => runAudit(),
    verify: (h: string) => verify(h),
    decide: (h: string, fid: string, rationale: string, approved: boolean) =>
      decide(h, fid, rationale, approved),
  },
}));
vi.mock('../../../audit/sampleAuditBatch', () => ({ SAMPLE_AUDIT_BATCH: { tenant_id: 'demo' } }));

import AuditPanel from '../../panels/AuditPanel';

const REPORT = {
  record_hash: '0123456789abcdef0123456789abcdef',
  signature_status: 'signed',
  n_findings: 2,
  findings: [
    { finding_id: 'f-low', pcaob_standard: 'AS 2201', risk_level: 'Low', description: 'orders mismatch', evidence_payload: { delta: 3 }, requires_human_review: false },
    { finding_id: 'f-crit', pcaob_standard: 'AS 2401', risk_level: 'Critical', description: 'unrecorded liability', evidence_payload: { amount: 9_000_000 }, requires_human_review: true },
  ],
};

const props = { api: {}, params: {}, containerApi: {}, group: {} } as unknown as React.ComponentProps<
  typeof AuditPanel
>;

describe('AuditPanel render contract', () => {
  it('mounts without throwing and shows the deck', () => {
    render(<AuditPanel {...props} />);
    expect(screen.getByTestId('audit-panel')).toBeInTheDocument();
  });

  it('starts in the honest unverified state (never asserts verified from the signature alone)', () => {
    const { container } = render(<AuditPanel {...props} />);
    expect(container.querySelector('.aud-verify-unverified')).not.toBeNull();
    expect(container.querySelector('.aud-verify-verified')).toBeNull();
  });

  it('runs an audit and lists findings highest-risk-first', async () => {
    runAudit.mockResolvedValue(REPORT);
    const { container } = render(<AuditPanel {...props} />);
    fireEvent.click(screen.getByText(/Run audit/));
    await waitFor(() => expect(screen.getByText(/unrecorded liability/)).toBeInTheDocument());
    const risks = Array.from(container.querySelectorAll('.aud-finding-risk')).map((n) => n.textContent);
    // Critical must be triaged ahead of Low regardless of report order.
    expect(risks[0]).toBe('Critical');
  });

  it('stays unverified until verify() returns, then reflects the real result', async () => {
    runAudit.mockResolvedValue(REPORT);
    verify.mockResolvedValue({ verified: true });
    const { container } = render(<AuditPanel {...props} />);
    fireEvent.click(screen.getByText(/Run audit/));
    await waitFor(() => expect(screen.getByText(/unrecorded liability/)).toBeInTheDocument());
    // Still unverified right after a fresh report.
    expect(container.querySelector('.aud-verify-unverified')).not.toBeNull();
    fireEvent.click(screen.getByText(/Verify signature/));
    await waitFor(() => expect(container.querySelector('.aud-verify-verified')).not.toBeNull());
  });
});
