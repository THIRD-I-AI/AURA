import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { EngagementsLaunchpad } from '../EngagementsLaunchpad';

describe('EngagementsLaunchpad', () => {
  it('renders the audit-lifecycle entry points', () => {
    render(<MemoryRouter><EngagementsLaunchpad onNavigate={() => {}} /></MemoryRouter>);
    expect(screen.getByTestId('engagements-launchpad')).toBeInTheDocument();
    expect(screen.getByTestId('launch-counterfactual')).toBeInTheDocument();
    expect(screen.getByTestId('launch-exceptions')).toBeInTheDocument();
    // "Audit your own data" is a public wizard route → a real link, not in-app nav.
    expect(screen.getByTestId('launch-own-data')).toHaveAttribute('href', '/audit/new');
  });

  it('routes in-app actions through onNavigate (not a fabricated run list)', () => {
    const onNavigate = vi.fn();
    render(<MemoryRouter><EngagementsLaunchpad onNavigate={onNavigate} /></MemoryRouter>);
    fireEvent.click(screen.getByTestId('launch-counterfactual'));
    expect(onNavigate).toHaveBeenCalledWith('counterfactual');
    fireEvent.click(screen.getByTestId('launch-exceptions'));
    expect(onNavigate).toHaveBeenCalledWith('audit-hitl');
  });
});
