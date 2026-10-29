import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../AuthContext';
import { AuthForm } from '../AuthForm';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { authService, setAuthToken } from '../../services/api';

function renderAt(path: string, state: unknown, mode: 'login' | 'signup') {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[{ pathname: path, state }]}>
        <AuthProvider>
          <Routes>
            <Route path={path} element={<AuthForm mode={mode} />} />
            <Route path="/" element={<div>landing</div>} />
            <Route path="/workbench" element={<div>workbench</div>} />
            <Route path="/verify/:hash" element={<div>verify page</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe('AuthForm flows from the landing page', () => {
  beforeEach(() => setAuthToken(null));
  afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

  it('prefills the email the landing page handed over in router state', () => {
    renderAt('/signup', { email: 'ann@bank.com' }, 'signup');
    expect(screen.getByTestId('auth-email')).toHaveValue('ann@bank.com');
  });

  it('sends a visitor who signed up to run an audit back to the landing page', async () => {
    vi.spyOn(authService, 'register').mockResolvedValue({ id: 'u1', email: 'ann@bank.com', name: 'Ann' } as never);
    renderAt('/signup', { next: 'audit', scenario: 'compas_recidivism' }, 'signup');
    await userEvent.type(screen.getByTestId('auth-name'), 'Ann');
    await userEvent.type(screen.getByTestId('auth-email'), 'ann@bank.com');
    await userEvent.type(screen.getByTestId('auth-password'), 'long-enough-pw');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByText('landing')).toBeInTheDocument();
  });

  it('checks a certificate hash from the login page without signing in', async () => {
    renderAt('/login', null, 'login');
    await userEvent.type(screen.getByLabelText(/Holding a certificate/), 'ABCDEF0123456789');
    await userEvent.click(screen.getByRole('button', { name: 'Verify' }));
    expect(await screen.findByText('verify page')).toBeInTheDocument();
  });

  it('ignores a value that is not a hash', async () => {
    renderAt('/login', null, 'login');
    await userEvent.type(screen.getByLabelText(/Holding a certificate/), 'not a hash');
    await userEvent.click(screen.getByRole('button', { name: 'Verify' }));
    expect(screen.queryByText('verify page')).toBeNull();
  });

  it('offers the theme switch', () => {
    renderAt('/login', null, 'login');
    expect(screen.getByTestId('theme-toggle')).toBeInTheDocument();
  });
});
