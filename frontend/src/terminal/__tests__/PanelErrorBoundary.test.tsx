import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PanelErrorBoundary } from '../PanelErrorBoundary';

function Boom({ explode }: { explode: boolean }) {
  if (explode) throw new Error('panel boom');
  return <div data-testid="ok">ok</div>;
}

describe('PanelErrorBoundary', () => {
  it('catches a child error and isolates it to an in-panel card', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <PanelErrorBoundary panelTitle="Query">
        <Boom explode={true} />
      </PanelErrorBoundary>,
    );
    expect(screen.getByTestId('panel-error')).toBeInTheDocument();
    expect(screen.getByText(/Query/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reload panel/i })).toBeInTheDocument();
    spy.mockRestore();
  });

  it('renders children when they do not throw', () => {
    render(
      <PanelErrorBoundary panelTitle="Query">
        <Boom explode={false} />
      </PanelErrorBoundary>,
    );
    expect(screen.getByTestId('ok')).toBeInTheDocument();
  });
});
