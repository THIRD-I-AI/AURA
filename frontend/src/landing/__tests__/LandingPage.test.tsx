import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

let signedIn: { email: string } | null = null;
vi.mock('@/auth/AuthContext', () => ({ useAuth: () => ({ user: signedIn, isAuthenticated: signedIn !== null }) }));

import { LandingPage } from '../LandingPage';
import { auditApi } from '@/audit/auditApi';
import { ThemeProvider } from '@/contexts/ThemeContext';

const SCENARIOS = [
  { id: 'compas_recidivism', title: 'COMPAS Recidivism', vertical: 'criminal justice', description: 'd0' },
  { id: 'fair_lending', title: 'Fair Lending', vertical: 'compliance', description: 'd1' },
];

function renderPage() {
  return render(<ThemeProvider><MemoryRouter><LandingPage /></MemoryRouter></ThemeProvider>);
}

describe('LandingPage', () => {
  beforeEach(() => {
    navigate.mockClear();
    signedIn = null;
    vi.spyOn(auditApi, 'publicKey').mockResolvedValue({ public_key_pem: '-----BEGIN PUBLIC KEY-----\nQUJDRA==\n-----END PUBLIC KEY-----\n' });
  });
  afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

  it('says what AURA is in its one h1', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([]);
    renderPage();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Causal audits anyone can verify');
  });

  it('lists one row per scenario and says which data is real', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue(SCENARIOS);
    renderPage();
    const compas = await screen.findByTestId('scenario-row-compas_recidivism');
    expect(compas).toHaveTextContent('Real ProPublica records');
    expect(screen.getByTestId('scenario-row-fair_lending')).toHaveTextContent('Synthetic, known effect');
  });

  it('sends a visitor who is not signed in to sign-up instead of a failing run', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue(SCENARIOS);
    const runSpy = vi.spyOn(auditApi, 'runScenario');
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: 'Run the COMPAS audit' })[0]);
    expect(navigate).toHaveBeenCalledWith('/signup', { state: { next: 'audit', scenario: 'compas_recidivism' } });
    expect(runSpy).not.toHaveBeenCalled();
  });

  it('runs the scenario for a signed-in user and opens its job', async () => {
    signedIn = { email: 'a@b.c' };
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue(SCENARIOS);
    vi.spyOn(auditApi, 'runScenario').mockResolvedValue({ job_id: 'ca_9', scenario_id: 'fair_lending', degraded: false });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Run the Fair Lending audit' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/audit/ca_9'));
  });

  // BUG-255: a failed run was shown as "Couldn't load scenarios" and hid the list.
  it('a failed run names its cause and keeps the scenarios on screen', async () => {
    signedIn = { email: 'a@b.c' };
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue(SCENARIOS);
    vi.spyOn(auditApi, 'runScenario').mockRejectedValue(new Error('HTTP 429: busy'));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Run the Fair Lending audit' }));
    expect(await screen.findByTestId('scenario-run-error')).toHaveTextContent('Too many audits are running.');
    expect(screen.queryByTestId('scenarios-error')).not.toBeInTheDocument();
    expect(screen.getByTestId('scenario-row-fair_lending')).toBeInTheDocument();
  });

  it('offers a retry when the scenarios fail to load', async () => {
    const list = vi.spyOn(auditApi, 'listScenarios').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(SCENARIOS);
    renderPage();
    expect(await screen.findByTestId('scenarios-error')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByTestId('scenario-row-compas_recidivism')).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('the seal stops matching when the verdict is edited, and can be restored', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([]);
    renderPage();
    const status = await screen.findByText(/Matches the seal/);
    const verdict = screen.getByLabelText('Verdict — edit it');
    await userEvent.type(verdict, '!');
    await waitFor(() => expect(status).toHaveTextContent('no longer matches'));
    await userEvent.click(screen.getByRole('button', { name: 'Restore the sealed text' }));
    await waitFor(() => expect(status).toHaveTextContent('Matches the seal'));
  });

  it('keeps the sign-up email out of the URL', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([]);
    renderPage();
    await userEvent.type(screen.getByLabelText('Work email'), 'ann@bank.com');
    await userEvent.click(screen.getByRole('button', { name: 'today' }));
    expect(navigate).toHaveBeenCalledWith('/signup', { state: { email: 'ann@bank.com' } });
  });

  it('switches between dark and light', async () => {
    vi.spyOn(auditApi, 'listScenarios').mockResolvedValue([]);
    renderPage();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    await userEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });
});
