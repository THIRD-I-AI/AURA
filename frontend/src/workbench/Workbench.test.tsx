/* Workbench (Claude Design port) — login → boot → cockpit flow + panel smoke. */
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../services/api', () => ({
  chatService: { streamMessage: vi.fn().mockResolvedValue(undefined) },
  authService: { login: vi.fn() },
}));

import { authService } from '../services/api';
import Workbench from './Workbench';

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false } as Response));
});

describe('Workbench', () => {
  it('starts at the login screen with the design headline', () => {
    render(<Workbench />);
    expect(screen.getByTestId('wb-login')).toBeInTheDocument();
    expect(screen.getByText('Analytics your auditors can replay.')).toBeInTheDocument();
  });

  it('email sign-in calls the REAL auth service; failure shows the error', async () => {
    vi.mocked(authService.login).mockRejectedValueOnce(new Error('Invalid credentials'));
    render(<Workbench />);
    fireEvent.change(screen.getByPlaceholderText('you@acme.com'), { target: { value: 'a@b.co' } });
    fireEvent.change(screen.getByPlaceholderText('••••••••••••'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByText('Continue'));
    expect(await screen.findByText('Invalid credentials')).toBeInTheDocument();
    expect(authService.login).toHaveBeenCalledWith('a@b.co', 'pw');
  });

  it('email sign-in success enters the boot sequence', async () => {
    vi.mocked(authService.login).mockResolvedValueOnce({} as never);
    render(<Workbench />);
    fireEvent.change(screen.getByPlaceholderText('you@acme.com'), { target: { value: 'a@b.co' } });
    fireEvent.click(screen.getByText('Continue'));
    expect(await screen.findByTestId('wb-boot')).toBeInTheDocument();
  });

  it('rejects an invalid email, then boots into the cockpit via SSO', async () => {
    vi.useFakeTimers();
    render(<Workbench />);
    fireEvent.click(screen.getByText('Continue'));
    expect(screen.getByText('Enter a valid corporate email address.')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Continue with Okta'));
    expect(screen.getByTestId('wb-boot')).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(420 * 7); });
    expect(screen.getByTestId('wb-app')).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('cockpit renders every board panel', async () => {
    vi.useFakeTimers();
    render(<Workbench />);
    fireEvent.click(screen.getByText('Continue with Okta'));
    await act(async () => { vi.advanceTimersByTime(420 * 7); });
    for (const id of ['wb-stats', 'wb-chat', 'wb-cf', 'wb-healing', 'wb-pipes', 'wb-lineage', 'wb-history', 'wb-feed']) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
    vi.useRealTimers();
  });

  it('healing approve signs the override and clears the pending badge state', async () => {
    vi.useFakeTimers();
    render(<Workbench />);
    fireEvent.click(screen.getByText('Continue with Okta'));
    await act(async () => { vi.advanceTimersByTime(420 * 7); });
    const approves = screen.getAllByText('Approve & deploy');
    fireEvent.click(approves[0]);
    expect(screen.getByText('✓ deployed — override signed to WORM log')).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('nav switches to a stub view that links back to the classic app', async () => {
    vi.useFakeTimers();
    render(<Workbench />);
    fireEvent.click(screen.getByText('Continue with Okta'));
    await act(async () => { vi.advanceTimersByTime(420 * 7); });
    fireEvent.click(screen.getByText('Connectors'));
    expect(screen.getByTestId('wb-stub')).toBeInTheDocument();
    expect(screen.getByText('Open in classic app →')).toHaveAttribute('href', '/app');
    vi.useRealTimers();
  });
});
