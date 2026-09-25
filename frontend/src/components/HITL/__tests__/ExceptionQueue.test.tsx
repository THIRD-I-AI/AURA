import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    subjectHistory: vi.fn(),
  },
  // Real implementation, not a mock: ExceptionQueue uses it for client-side
  // hash validation and the behavior under test depends on its actual regex.
  sanitizeRecordHash: (value: unknown): string | null =>
    typeof value === 'string' && /^[0-9a-f]{64}$/.test(value) ? value : null,
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
    await userEvent.click(screen.getByRole('button', { name: /run demo scenario/i }));
    await waitFor(() => expect(screen.getByText(/AS 2201/)).toBeInTheDocument());
    expect(screen.getByText(/signature verified/i)).toBeInTheDocument();
    expect(screen.getByText(/Pending exceptions \(1\)/)).toBeInTheDocument();
    expect(svc.runAudit).toHaveBeenCalledOnce();
    expect(svc.getExceptions).toHaveBeenCalledWith(REPORT.record_hash);
  });

  it('requires a rationale before decision buttons enable', async () => {
    render(<ExceptionQueue />);
    await userEvent.click(screen.getByRole('button', { name: /run demo scenario/i }));
    await waitFor(() => screen.getByText(/AS 2201/));
    await userEvent.click(screen.getByText(/AS 2201/));
    const approve = screen.getByRole('button', { name: /approve ai finding/i });
    expect(approve).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText(/approving or overriding/i), 'verified with vendor');
    expect(approve).toBeEnabled();
  });

  it('submits an override and refreshes the queue to empty', async () => {
    render(<ExceptionQueue />);
    await userEvent.click(screen.getByRole('button', { name: /run demo scenario/i }));
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
    await userEvent.click(screen.getByRole('button', { name: /run demo scenario/i }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/503/));
  });

  it('loads an existing audit by record hash and lists its pending exceptions', async () => {
    render(<ExceptionQueue />);
    await userEvent.type(screen.getByLabelText(/record hash/i), REPORT.record_hash);
    await userEvent.click(screen.getByRole('button', { name: /load audit/i }));
    await waitFor(() => expect(screen.getByText(/AS 2201/)).toBeInTheDocument());
    expect(svc.getExceptions).toHaveBeenCalledWith(REPORT.record_hash);
    expect(svc.runAudit).not.toHaveBeenCalled();
  });

  it('shows an inline validation message for a malformed record hash without throwing or calling the service', async () => {
    render(<ExceptionQueue />);
    await userEvent.type(screen.getByLabelText(/record hash/i), 'not-a-real-hash');
    await userEvent.click(screen.getByRole('button', { name: /load audit/i }));
    expect(await screen.findByText(/64-character sha256 hex/i)).toBeInTheDocument();
    expect(svc.getExceptions).not.toHaveBeenCalled();
  });

  it('runs an audit against user-pasted ledger JSON', async () => {
    render(<ExceptionQueue />);
    // fireEvent.change, not userEvent.type: userEvent.type parses `{}` as
    // special-key sequences, which mangles literal JSON braces.
    fireEvent.change(screen.getByLabelText(/ledger json/i), {
      target: { value: '{"ledger":[{"internal_id":"L-1","account_code":"4000","amount":1}]}' },
    });
    await userEvent.click(screen.getByRole('button', { name: /run my audit/i }));
    await waitFor(() => expect(svc.runAudit).toHaveBeenCalledOnce());
    expect(svc.runAudit.mock.calls[0][0]).toMatchObject({
      ledger: [{ internal_id: 'L-1', account_code: '4000', amount: 1 }],
    });
    await waitFor(() => expect(screen.getByText(/AS 2201/)).toBeInTheDocument());
  });

  // BUG-158: the only real-data path was a raw JSON textarea.
  describe('uploading ledger files', () => {
    const csv = (name: string, body: string) => new File([body], name, { type: 'text/csv' });
    const pick = (label: RegExp, file: File) => userEvent.upload(screen.getByLabelText(label), file);

    beforeEach(() => {
      svc.runAudit.mockResolvedValue(REPORT);
      svc.getExceptions.mockResolvedValue(queueBefore);
      svc.verify.mockResolvedValue({ verified: true });
    });

    it('parses a CSV, shows its row count and columns, and audits the parsed rows with numeric amounts', async () => {
      render(<ExceptionQueue />);
      await pick(/^general ledger$/i, csv('gl.csv', 'internal_id,account_code,amount\nL-1,4000,"1,250.00"\nL-2,5000,75\n'));

      const status = await screen.findByTestId('ledger-file-status-ledger');
      expect(status).toHaveTextContent('2 rows');
      expect(status).toHaveTextContent('internal_id, account_code, amount');

      await userEvent.click(screen.getByRole('button', { name: /run my audit/i }));
      await waitFor(() => expect(svc.runAudit).toHaveBeenCalledOnce());
      expect(svc.runAudit.mock.calls[0][0]).toMatchObject({
        ledger: [
          { internal_id: 'L-1', account_code: '4000', amount: 1250 },
          { internal_id: 'L-2', account_code: '5000', amount: 75 },
        ],
      });
    });

    it('explains a bad file and blocks the run instead of silently auditing without it', async () => {
      render(<ExceptionQueue />);
      await pick(/^invoices$/i, csv('inv.csv', 'invoice_number,po_number,amount\nI-1,P-1,12\nI-2,P-2,twelve\n'));

      const alert = await within(screen.getByTestId('ledger-file-invoices')).findByRole('alert');
      expect(alert).toHaveTextContent(/row 2: amount "twelve"/);

      await userEvent.click(screen.getByRole('button', { name: /run my audit/i }));
      expect(await screen.findByText(/fix or remove invoices before running/i)).toBeInTheDocument();
      expect(svc.runAudit).not.toHaveBeenCalled();
    });

    // BUG-167: headers that don't match backend field names.
    it('auto-maps a same-named header, and lets the user map a differently named one before auditing', async () => {
      render(<ExceptionQueue />);
      await pick(/^invoices$/i, csv('inv.csv', 'Invoice Number,Our Ref,Total\nI-1,P-1,12\n'));
      const box = screen.getByTestId('ledger-file-invoices');

      expect(within(box).getByLabelText('invoice_number column')).toHaveValue('Invoice Number');
      expect(within(box).getByLabelText('po_number column')).toHaveValue('');
      await userEvent.selectOptions(within(box).getByLabelText('po_number column'), 'Our Ref');
      await userEvent.selectOptions(within(box).getByLabelText('amount column'), 'Total');
      expect(within(box).queryByRole('note')).toBeNull();

      await userEvent.click(screen.getByRole('button', { name: /run my audit/i }));
      await waitFor(() => expect(svc.runAudit).toHaveBeenCalledOnce());
      expect(svc.runAudit.mock.calls[0][0]).toMatchObject({
        invoices: [{ invoice_number: 'I-1', po_number: 'P-1', amount: 12 }],
      });
    });

    it('warns, without blocking, when a recommended column is missing', async () => {
      render(<ExceptionQueue />);
      await pick(/^invoices$/i, csv('inv.csv', 'invoice_number,amount\nI-1,5\n'));
      const note = await within(screen.getByTestId('ledger-file-invoices')).findByRole('note');
      expect(note).toHaveTextContent(/no po_number column/i);
      await userEvent.click(screen.getByRole('button', { name: /run my audit/i }));
      await waitFor(() => expect(svc.runAudit).toHaveBeenCalledOnce());
    });

    it('lets an uploaded file override the same key pasted as JSON while keeping the other pasted keys', async () => {
      render(<ExceptionQueue />);
      fireEvent.change(screen.getByLabelText(/ledger json/i), {
        target: { value: '{"ledger":[{"internal_id":"OLD","amount":1}],"purchase_orders":[{"po_number":"P-1"}]}' },
      });
      await pick(/^general ledger$/i, csv('gl.csv', 'internal_id,account_code,amount\nNEW,4000,2\n'));
      await screen.findByTestId('ledger-file-status-ledger');
      await userEvent.click(screen.getByRole('button', { name: /run my audit/i }));
      await waitFor(() => expect(svc.runAudit).toHaveBeenCalledOnce());
      const sent = svc.runAudit.mock.calls[0][0];
      expect(sent.ledger).toEqual([{ internal_id: 'NEW', account_code: '4000', amount: 2 }]);
      expect(sent.purchase_orders).toEqual([{ po_number: 'P-1' }]);
    });

    it('disables Run again once the only file is removed', async () => {
      render(<ExceptionQueue />);
      const run = screen.getByRole('button', { name: /run my audit/i });
      expect(run).toBeDisabled();
      await pick(/^general ledger$/i, csv('gl.csv', 'internal_id,account_code,amount\nL-1,4000,1\n'));
      await screen.findByTestId('ledger-file-status-ledger');
      expect(run).toBeEnabled();
      await userEvent.click(within(screen.getByTestId('ledger-file-ledger')).getByRole('button', { name: /remove/i }));
      expect(screen.queryByTestId('ledger-file-status-ledger')).not.toBeInTheDocument();
      expect(run).toBeDisabled();
    });
  });

  // BUG-159: per-subject ledger history existed server-side with no UI.
  describe('subject audit history', () => {
    const HISTORY = {
      tenant_id: 't1', subject_id: 'loan-model-v3', count: 2,
      audits: [
        { seq: 1, kind: 'financial_audit_completed', subject_type: 'dataset', preparer_id: 'system',
          reviewer_id: null, cert_hash: 'a'.repeat(64), input_fingerprint: 'f1', ts: '2026-09-01T10:00:00', record_hash: 'r1' },
        { seq: 4, kind: 'human_review', subject_type: 'dataset', preparer_id: 'system',
          reviewer_id: 'auditor-7', cert_hash: 'b'.repeat(64), input_fingerprint: 'f2', ts: '2026-09-02T09:30:00', record_hash: 'r2' },
      ],
    };

    it('looks up a subject and lists its audits oldest-first with reviewer and certificate links', async () => {
      svc.subjectHistory.mockResolvedValue(HISTORY);
      render(<ExceptionQueue />);
      const button = screen.getByRole('button', { name: /look up history/i });
      expect(button).toBeDisabled();
      await userEvent.type(screen.getByLabelText(/subject to look up/i), '  loan-model-v3  ');
      await userEvent.click(button);

      const table = await screen.findByTestId('subject-history-table');
      expect(svc.subjectHistory).toHaveBeenCalledWith('loan-model-v3');
      expect(table).toHaveTextContent('2 audits for "loan-model-v3", oldest first');
      const rows = within(table).getAllByRole('row').slice(1);
      expect(rows[0]).toHaveTextContent('financial_audit_completed');
      expect(rows[1]).toHaveTextContent('auditor-7');
      const link = within(rows[0]).getByRole('link');
      expect(link).toHaveAttribute('href', `/certificate/${'a'.repeat(64)}`);
      expect(link).toHaveTextContent('aaaaaaaaaaaa…');
    });

    it('says plainly when a subject has no audits', async () => {
      svc.subjectHistory.mockResolvedValue({ tenant_id: 't1', subject_id: 'nope', count: 0, audits: [] });
      render(<ExceptionQueue />);
      await userEvent.type(screen.getByLabelText(/subject to look up/i), 'nope');
      await userEvent.click(screen.getByRole('button', { name: /look up history/i }));
      expect(await screen.findByTestId('subject-history-empty')).toHaveTextContent(/no audits recorded for "nope"/i);
    });

    it('shows a lookup failure as an alert', async () => {
      svc.subjectHistory.mockRejectedValue(new Error('HTTP 401: unauthorized'));
      render(<ExceptionQueue />);
      await userEvent.type(screen.getByLabelText(/subject to look up/i), 'x');
      await userEvent.click(screen.getByRole('button', { name: /look up history/i }));
      expect(await screen.findByText(/HTTP 401/)).toHaveAttribute('role', 'alert');
    });
  });

  it('shows an inline parse error for malformed ledger JSON without crashing', async () => {
    render(<ExceptionQueue />);
    fireEvent.change(screen.getByLabelText(/ledger json/i), { target: { value: '{not valid json' } });
    await userEvent.click(screen.getByRole('button', { name: /run my audit/i }));
    expect(await screen.findByText(/invalid json/i)).toBeInTheDocument();
    expect(svc.runAudit).not.toHaveBeenCalled();
  });
});
