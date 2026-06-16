import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../../AppRoutes';

describe('AppRoutes', () => {
  it('renders the public front door at /', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('audit-front-door')).toBeInTheDocument();
  });

  it('renders the verify page at /verify/:hash', () => {
    render(
      <MemoryRouter initialEntries={['/verify/abc123']}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('verify-page')).toBeInTheDocument();
  });

  it('gates an anonymous deep-link into /app behind login', async () => {
    // /app/* is wrapped in ProtectedRoute (Mounith's SaaS auth). With no
    // authenticated session, a deep link to an internal page must redirect
    // to /login rather than mount the dashboard. (An authenticated deep-link
    // mounting the right page is covered once an AuthProvider is in scope.)
    render(
      <MemoryRouter initialEntries={['/app/chat']}>
        <AppRoutes />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId('auth-form')).toBeInTheDocument());
    expect(screen.queryByTestId('audit-progress')).not.toBeInTheDocument();
  });
});
