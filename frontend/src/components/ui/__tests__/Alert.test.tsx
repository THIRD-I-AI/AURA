import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Alert } from '../Alert';

describe('Alert', () => {
  it('renders title and type class', () => {
    const { container } = render(<Alert type="error" title="Oops" />);
    expect(screen.getByText('Oops')).toBeInTheDocument();
    expect(container.querySelector('.alert-error')).toBeTruthy();
  });

  it('renders optional message', () => {
    render(<Alert type="info" title="Note" message="Details here" />);
    expect(screen.getByText('Details here')).toBeInTheDocument();
  });

  it('has role="alert"', () => {
    render(<Alert type="warning" title="Watch out" />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('calls onClose when dismiss button clicked', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Alert type="success" title="Done" onClose={onClose} />);
    await user.click(screen.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('renders action button', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Alert type="info" title="Tip" action={{ label: 'Retry', onClick }} />);
    await user.click(screen.getByText('Retry'));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('does not show close button when onClose is undefined', () => {
    render(<Alert type="info" title="No close" />);
    expect(screen.queryByLabelText('Close')).toBeNull();
  });
});
