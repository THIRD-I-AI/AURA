import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { AuthProvider } from '../AuthContext';
import { AuthForm } from '../AuthForm';
import { ProtectedRoute } from '../ProtectedRoute';
import { setAuthToken } from '../../services/api';

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
