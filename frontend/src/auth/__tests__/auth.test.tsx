import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { AuthProvider } from '../AuthContext';
import { AuthForm } from '../AuthForm';
import { AuthNav } from '../AuthNav';
import { ProtectedRoute } from '../ProtectedRoute';
import { setAuthToken } from '../../services/api';

/** A decodable (unsigned) JWT-shaped token for tests — decodeAuthToken only
 *  reads the claims segment, so this is enough to simulate a signed-in user. */
function fakeToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims))}.s`;
}

describe('AuthForm', () => {
  beforeEach(() => setAuthToken(null));

  it('login mode collects email + password but not a name', () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthForm mode="login" />
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('auth-email')).toBeTruthy();
    expect(screen.getByTestId('auth-password')).toBeTruthy();
    expect(screen.queryByTestId('auth-name')).toBeNull();
    expect(screen.getByRole('button').textContent).toMatch(/sign in/i);
  });

  it('signup mode also collects a name', () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthForm mode="signup" />
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('auth-name')).toBeTruthy();
    expect(screen.getByRole('button').textContent).toMatch(/create account/i);
  });
});

describe('ProtectedRoute', () => {
  beforeEach(() => setAuthToken(null));

  it('redirects an anonymous visitor to /login', () => {
    render(
      <MemoryRouter initialEntries={['/app']}>
        <AuthProvider>
          <Routes>
            <Route path="/app" element={<ProtectedRoute><div>secret dashboard</div></ProtectedRoute>} />
            <Route path="/login" element={<div>login page</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText('login page')).toBeTruthy();
    expect(screen.queryByText('secret dashboard')).toBeNull();
  });
});

describe('AuthNav', () => {
  beforeEach(() => setAuthToken(null));

  it('offers a way in when logged out', () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthNav />
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('auth-nav-login')).toBeTruthy();
    expect(screen.getByTestId('auth-nav-signup')).toBeTruthy();
    expect(screen.queryByTestId('auth-nav-logout')).toBeNull();
  });

  it('offers workspace + sign out when logged in', () => {
    setAuthToken(fakeToken({ sub: 'u1', name: 'Ada', org_id: 'org-a' }));
    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthNav />
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('auth-nav-workspace')).toBeTruthy();
    expect(screen.getByTestId('auth-nav-logout')).toBeTruthy();
    expect(screen.queryByTestId('auth-nav-login')).toBeNull();
  });
});
