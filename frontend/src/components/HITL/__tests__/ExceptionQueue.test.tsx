import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach } from 'vitest';

const REPORT = {
  record_hash: 'a'.repeat(64),
  signature_status: 'signed',
  n_findings: 2,
  findings: [],
};

const FINDING = {
  finding_id: 'b'.repeat(64),
  pcaob_standard: 'AS 2201',
  risk_level: 'Medium',
  description: 'Invoice INV-9001 lacks a matching Purchase Order.',
  evidence_payload: { invoice: { employee_name: 'PII-abc123def456' } },
  requires_human_review: true,
};

const queueBefore = { record_hash: REPORT.record_hash, pending: [FINDING], n_pending: 1, n_decided: 0 };
const queueAfter = { record_hash: REPORT.record_hash, pending: [], n_pending: 0, n_decided: 1 };

vi.mock('../../../services/api', () => ({
  financialAuditService: {
    ensureAuditorToken: vi.fn().mockResolvedValue(undefined),
    runAudit: vi.fn(),
    getExceptions: vi.fn(),
    decide: vi.fn(),
    verify: vi.fn(),
  },
}));

import { financialAuditService } from '../../../services/api';
import ExceptionQueue from '../ExceptionQueue';

const svc = financialAuditService as unknown as Record<string, ReturnType<typeof vi.fn>>;

describe('ExceptionQueue (HITL workbench)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    svc.ensureAuditorToken.mockResolvedValue(undefined);
    svc.runAudit.mockResolvedValue(REPORT);
    svc.getExceptions.mockResolvedValue(queueBefore);
    svc.verify.mockResolvedValue({ verified: true, record_hash: REPORT.record_hash });
    svc.decide.mockResolvedValue({
      record_hash: 'c'.repeat(64), document_type: 'HumanOverrideRecord',
      signature_status: 'signed', human_auditor_id: 'auditor-demo',
      finding_id: FINDING.finding_id, approved: false,
    });
  });

  it('runs a sample audit and lists pending exceptions with verification badge', async () => {
    render(<ExceptionQueue />);
    await userEvent.click(screen.getByRole('button', { name: /run sample financial audit/i }));
    await waitFor(() => expect(screen.getByText(/AS 2201/)).toBeInTheDocument());
    expect(screen.getByText(/signature verified/i)).toBeInTheDocument();
    expect(screen.getByText(/Pending exceptions \(1\)/)).toBeInTheDocument();
    expect(svc.runAudit).toHaveBeenCalledOnce();
    expect(svc.getExceptions).toHaveBeenCalledWith(REPORT.record_hash);
  });

  it('requires a rationale before decision buttons enable', async () => {
    render(<ExceptionQueue />);
    await userEvent.click(screen.getByRole('button', { name: /run sample financial audit/i }));
    await waitFor(() => screen.getByText(/AS 2201/));
    await userEvent.click(screen.getByText(/AS 2201/));
    const approve = screen.getByRole('button', { name: /approve ai finding/i });
    expect(approve).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText(/approving or overriding/i), 'verified with vendor');
    expect(approve).toBeEnabled();
  });

  it('submits an override and refreshes the queue to empty', async () => {
    render(<ExceptionQueue />);
    await userEvent.click(screen.getByRole('button', { name: /run sample financial audit/i }));
    await waitFor(() => screen.getByText(/AS 2201/));
    await userEvent.click(screen.getByText(/AS 2201/));
    await userEvent.type(screen.getByPlaceholderText(/approving or overriding/i), 'duplicate is a re-issued invoice');
    svc.getExceptions.mockResolvedValue(queueAfter);
    await userEvent.click(screen.getByRole('button', { name: /override ai finding/i }));
    await waitFor(() => expect(screen.getByText(/All exceptions cleared/i)).toBeInTheDocument());
    expect(svc.decide).toHaveBeenCalledWith(
      REPORT.record_hash, FINDING.finding_id, 'duplicate is a re-issued invoice', false,
    );
    expect(screen.getByText(/HumanOverrideRecord/i)).toBeInTheDocument();
  });

  it('surfaces backend errors', async () => {
    svc.runAudit.mockRejectedValue(new Error('HTTP 503: service unavailable'));
    render(<ExceptionQueue />);
    await userEvent.click(screen.getByRole('button', { name: /run sample financial audit/i }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/503/));
  });
});
