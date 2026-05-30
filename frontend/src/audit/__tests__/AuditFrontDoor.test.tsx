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
});
