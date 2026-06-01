import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

import { AuditWizard } from '../AuditWizard';
import { auditApi } from '../auditApi';

function csvFile() {
  return new File(['protected_class,approved,income,officer\n1,0,50000,A\n0,1,60000,B\n'], 'loans.csv', { type: 'text/csv' });
}

describe('AuditWizard (audit your own data)', () => {
  afterEach(() => { vi.restoreAllMocks(); navigate.mockClear(); });

  it('uploads a CSV, maps columns, and runs the audit', async () => {
    vi.spyOn(auditApi, 'uploadDataset').mockResolvedValue({ filename: 'loans.csv' });
    const run = vi.spyOn(auditApi, 'runDataAudit').mockResolvedValue({ job_id: 'audit_42' });
    const user = userEvent.setup();
    render(<MemoryRouter><AuditWizard /></MemoryRouter>);

    await user.upload(screen.getByTestId('wizard-file-input'), csvFile());
    await waitFor(() => expect(screen.getByTestId('wizard-preview')).toBeInTheDocument());
    await waitFor(() => expect(auditApi.uploadDataset).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId('wizard-next')).toBeEnabled());
    await user.click(screen.getByTestId('wizard-next'));

    await user.selectOptions(screen.getByTestId('map-treatment'), 'protected_class');
    await user.selectOptions(screen.getByTestId('map-outcome'), 'approved');
    await user.click(screen.getByTestId('confounder-income'));
    await user.click(screen.getByTestId('wizard-next'));

    await user.click(screen.getByTestId('wizard-run'));
    await waitFor(() => expect(run).toHaveBeenCalledWith(expect.objectContaining({
      uploaded_file: 'loans.csv', treatment: 'protected_class', outcome: 'approved', confounders: ['income'],
    })));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/audit/audit_42'));
  });

  it('blocks advancing past mapping until the mapping is valid', async () => {
    vi.spyOn(auditApi, 'uploadDataset').mockResolvedValue({ filename: 'loans.csv' });
    const user = userEvent.setup();
    render(<MemoryRouter><AuditWizard /></MemoryRouter>);
    await user.upload(screen.getByTestId('wizard-file-input'), csvFile());
    await waitFor(() => screen.getByTestId('wizard-preview'));
    await waitFor(() => expect(screen.getByTestId('wizard-next')).toBeEnabled());
    await user.click(screen.getByTestId('wizard-next'));
    expect(screen.getByTestId('wizard-next')).toBeDisabled();
    expect(screen.getByTestId('err-treatment')).toBeInTheDocument();
  });

  it('blocks a non-numeric treatment (e.g. raw COMPAS race strings)', async () => {
    vi.spyOn(auditApi, 'uploadDataset').mockResolvedValue({ filename: 'compas.csv' });
    const user = userEvent.setup();
    // Raw race is string-typed → backend coerces to NaN and drops every row.
    const file = new File(['race,recid,priors\nAfrican-American,1,3\nCaucasian,0,1\nHispanic,1,2\n'], 'compas.csv', { type: 'text/csv' });
    render(<MemoryRouter><AuditWizard /></MemoryRouter>);
    await user.upload(screen.getByTestId('wizard-file-input'), file);
    await waitFor(() => screen.getByTestId('wizard-preview'));
    await waitFor(() => expect(screen.getByTestId('wizard-next')).toBeEnabled());
    await user.click(screen.getByTestId('wizard-next'));

    await user.selectOptions(screen.getByTestId('map-treatment'), 'race');
    await user.selectOptions(screen.getByTestId('map-outcome'), 'recid');
    await user.click(screen.getByTestId('confounder-priors'));
    // Mapping is otherwise valid, but the numeric guard blocks a string treatment.
    expect(screen.getByTestId('err-treatment')).toHaveTextContent(/non-numeric|numbers/i);
    expect(screen.getByTestId('wizard-next')).toBeDisabled();
  });
});
