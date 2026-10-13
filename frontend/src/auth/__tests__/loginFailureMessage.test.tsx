import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../AuthContext';
import { AuthForm } from '../AuthForm';
import { ApiRequestError, authService } from '../../services/api';

// BUG-427: every login failure used to read "Wrong email or password."
async function failLoginWith(err: Error): Promise<string> {
  vi.spyOn(authService, 'login').mockRejectedValue(err);
  render(
    <MemoryRouter>
      <AuthProvider>
        <AuthForm mode="login" />
      </AuthProvider>
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByTestId('auth-email'), { target: { value: 'a@example.com' } });
  fireEvent.change(screen.getByTestId('auth-password'), { target: { value: 'correct-password' } });
  fireEvent.submit(screen.getByTestId('auth-email').closest('form')!);
  return (await screen.findByTestId('auth-error')).textContent ?? '';
}

describe('AuthForm login failure message', () => {
  afterEach(() => vi.restoreAllMocks());

  it('says the caller is rate limited on a 429', async () => {
    expect(await failLoginWith(new ApiRequestError('Too many requests', 429))).toMatch(/too many sign-in attempts/i);
  });

  it('says the server is unreachable on a network failure', async () => {
    expect(await failLoginWith(new ApiRequestError('Network error - backend may be offline', 0)))
      .toMatch(/cannot reach the server/i);
  });

  it('says the server is unreachable on a 503', async () => {
    expect(await failLoginWith(new ApiRequestError('Service unavailable', 503))).toMatch(/cannot reach the server/i);
  });

  it('still reports a 401 as wrong credentials', async () => {
    expect(await failLoginWith(new ApiRequestError('Unauthorized', 401))).toMatch(/wrong email or password/i);
  });
});
