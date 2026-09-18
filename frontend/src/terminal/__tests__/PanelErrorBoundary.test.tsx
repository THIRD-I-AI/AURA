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

  // BUG-110: a raw thrown Error's .message is untrusted (could embed a
  // backend/library internal detail) and must not reach the fallback verbatim.
  it('does not render a raw, unsanitized Error message', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <PanelErrorBoundary panelTitle="Query">
        <Boom explode={true} />
      </PanelErrorBoundary>,
    );
    expect(screen.queryByText('panel boom')).not.toBeInTheDocument();
    expect(screen.getByText('An unexpected error occurred.')).toBeInTheDocument();
    spy.mockRestore();
  });
});
