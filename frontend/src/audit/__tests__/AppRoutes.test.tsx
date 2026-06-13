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

  it('deep-links into an internal app page (lazy chunk mounts)', async () => {
    render(
      <MemoryRouter initialEntries={['/app/chat']}>
        <AppRoutes />
      </MemoryRouter>,
    );
    // App is lazy; once it loads, the shell brand renders. Asserting the
    // chunk mounts proves /app/* deep links resolve (panel-level behaviour
    // is covered by the panel tests).
    await waitFor(() => expect(screen.getAllByText('AURA').length).toBeGreaterThan(0), { timeout: 4000 });
  });
});
