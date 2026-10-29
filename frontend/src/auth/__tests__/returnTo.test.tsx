import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../AuthContext';
import { AuthForm } from '../AuthForm';
import { ProtectedRoute } from '../ProtectedRoute';
import { SsoCallback } from '../SsoCallback';
import { authService, setAuthToken } from '../../services/api';

function fakeToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims))}.s`;
}

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname + loc.search + loc.hash}</div>;
}

function renderAt(entry: string | { pathname: string; state: unknown }) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AuthProvider>
        <Routes>
          <Route path="/app/terminal" element={<ProtectedRoute><Where /></ProtectedRoute>} />
          <Route path="/workbench" element={<Where />} />
          <Route path="/login" element={<AuthForm mode="login" />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

async function signIn() {
  vi.spyOn(authService, 'login').mockImplementation(async () => {
    setAuthToken(fakeToken({ sub: 'u1' }));
    return { id: 'u1', name: 'u1', email: 'a@b.c', orgId: 'org', role: 'analyst' } as never;
  });
  fireEvent.change(screen.getByTestId('auth-email'), { target: { value: 'a@b.c' } });
  fireEvent.change(screen.getByTestId('auth-password'), { target: { value: 'pw' } });
  await act(async () => {
    fireEvent.submit(screen.getByTestId('auth-email').closest('form')!);
  });
}

describe('BUG-553: post-login return target keeps the query string', () => {
  beforeEach(() => {
    setAuthToken(null);
    sessionStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it('an anonymous deep link with ?panel= lands back on the same URL after login', async () => {
    renderAt('/app/terminal?panel=audit#focus');
    expect(screen.getByTestId('auth-email')).toBeTruthy();
    await signIn();
    await waitFor(() =>
      expect(screen.getByTestId('where').textContent).toBe('/app/terminal?panel=audit#focus'),
    );
  });

  it('refuses a protocol-relative `from` and falls back to /workbench', async () => {
    renderAt({ pathname: '/login', state: { from: '//evil.example/x' } });
    await signIn();
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/workbench'));
  });

  it('SSO sign-in returns to the deep link ProtectedRoute remembered', async () => {
    renderAt('/app/terminal?panel=pipeline');
    expect(sessionStorage.getItem('aura.returnTo')).toBe('/app/terminal?panel=pipeline');

    const replace = vi.fn();
    vi.stubGlobal('location', { ...window.location, hash: '#code=abc', replace });
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ access_token: fakeToken({ sub: 'u1' }) }),
    }) as unknown as typeof fetch;
    try {
      render(<SsoCallback />);
      await waitFor(() => expect(replace).toHaveBeenCalledWith('/app/terminal?panel=pipeline'));
      expect(sessionStorage.getItem('aura.returnTo')).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
