import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

import { AuditFrontDoor } from '../AuditFrontDoor';
import { auditApi } from '../auditApi';

describe('AuditFrontDoor', () => {
  beforeEach(() => { navigate.mockClear(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('renders one card per scenario', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([
      { id: 'fair_lending', title: 'Fair Lending', vertical: 'compliance', description: 'd1' },
      { id: 'insurance', title: 'Insurance', vertical: 'insurance', description: 'd2' },
    ]);
    render(<MemoryRouter><AuditFrontDoor /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('scenario-card-fair_lending')).toBeInTheDocument());
    expect(screen.getByTestId('scenario-card-insurance')).toBeInTheDocument();
  });

  it('clicking a card runs the scenario and navigates to its job', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([
      { id: 'fair_lending', title: 'Fair Lending', vertical: 'compliance', description: 'd1' },
    ]);
    vi.spyOn(auditApi, 'runScenario').mockResolvedValue({ job_id: 'ca_9', scenario_id: 'fair_lending', degraded: false });
    render(<MemoryRouter><AuditFrontDoor /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('scenario-card-fair_lending'));
    await userEvent.click(screen.getByTestId('scenario-card-fair_lending'));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/audit/ca_9'));
  });

  it('shows a retry affordance when scenarios fail to load', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockRejectedValue(new Error('offline'));
    render(<MemoryRouter><AuditFrontDoor /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('scenarios-error')).toBeInTheDocument());
  });

  it('shows a static signed-verifiable trust band (no faked live hash)', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([]);
    render(<MemoryRouter><AuditFrontDoor /></MemoryRouter>);
    const band = await screen.findByTestId('aud-trust-band');
    expect(band.textContent).toMatch(/ED25519/i);
    expect(band.textContent).toMatch(/verif/i);
  });

  // BUG-255: a failed run was shown as "Couldn't load scenarios" and hid the list.
  it('a failed run says so and keeps the scenarios on screen', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([
      { id: 'fair_lending', title: 'Fair Lending', vertical: 'compliance', description: 'd1' },
    ]);
    vi.spyOn(auditApi, 'runScenario').mockRejectedValue(new Error('HTTP 401: {"detail":"Not authenticated"}'));
    render(<MemoryRouter><AuditFrontDoor /></MemoryRouter>);
    await userEvent.click(await screen.findByTestId('scenario-card-fair_lending'));

    expect(await screen.findByTestId('scenario-run-error')).toHaveTextContent('Sign in to run an audit.');
    expect(screen.queryByTestId('scenarios-error')).not.toBeInTheDocument();
    expect(screen.getByTestId('scenario-card-fair_lending')).toBeEnabled();
    expect(screen.queryByText(/Not authenticated/)).not.toBeInTheDocument();
  });
});
