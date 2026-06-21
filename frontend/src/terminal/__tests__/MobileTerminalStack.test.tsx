import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// Stub the registry with synchronous panels so we test the stack's structure +
// tab switching without pulling in lazy panels / network calls. Defined inside
// the factory because vi.mock is hoisted above module-level declarations.
vi.mock('../panels/registry', () => {
  const stub = (label: string) => () => <div>{label} panel body</div>;
  const icon = () => <span data-testid="icon" />;
  return {
    PANEL_IDS: ['query', 'datasets'],
    PANEL_REGISTRY: {
      query: { title: 'Query', icon, component: stub('Query') },
      datasets: { title: 'Datasets', icon, component: stub('Datasets') },
    },
  };
});

import { MobileTerminalStack } from '../MobileTerminalStack';

describe('MobileTerminalStack', () => {
  it('renders the desktop-cockpit banner and one tab per panel', () => {
    render(<MobileTerminalStack />);
    expect(screen.getByRole('note')).toHaveTextContent(/desktop cockpit/i);
    expect(screen.getByRole('button', { name: /Query/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Datasets/ })).toBeInTheDocument();
  });

  it('shows the first panel by default and switches on tab click', () => {
    render(<MobileTerminalStack />);
    expect(screen.getByText('Query panel body')).toBeInTheDocument();
    expect(screen.queryByText('Datasets panel body')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Datasets/ }));

    expect(screen.getByText('Datasets panel body')).toBeInTheDocument();
    expect(screen.queryByText('Query panel body')).not.toBeInTheDocument();
  });
});
