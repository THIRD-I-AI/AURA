import { render, screen } from '@testing-library/react';
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
});
