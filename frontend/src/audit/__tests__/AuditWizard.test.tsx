import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

import { AuditWizard } from '../AuditWizard';
import { auditApi } from '../auditApi';

describe('AuditWizard', () => {
  afterEach(() => { vi.restoreAllMocks(); navigate.mockClear(); });

  it('submits the assembled query and navigates to the job', async () => {
    const spy = vi.spyOn(auditApi, 'submitCustomAudit').mockResolvedValue({ job_id: 'ca_7' });
    render(<MemoryRouter><AuditWizard /></MemoryRouter>);

    await userEvent.type(screen.getByTestId('wizard-treatment'), 'protected_class');
    await userEvent.type(screen.getByTestId('wizard-outcome'), 'approved');
    await userEvent.type(screen.getByTestId('wizard-confounders'), 'income, dti');
    await userEvent.click(screen.getByTestId('wizard-submit'));

    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.objectContaining({
      treatment: 'protected_class', outcome: 'approved', confounders: ['income', 'dti'],
    })));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/audit/ca_7'));
  });

  it('blocks submit until treatment and outcome are filled', async () => {
    render(<MemoryRouter><AuditWizard /></MemoryRouter>);
    expect(screen.getByTestId('wizard-submit')).toBeDisabled();
  });
});
