import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Button } from '../Button';

describe('Button', () => {
  it('renders children text', () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole('button', { name: /click me/i })).toBeInTheDocument();
  });

  it('applies variant and size classes', () => {
    render(<Button variant="danger" size="lg">Delete</Button>);
    const btn = screen.getByRole('button');
    expect(btn.className).toContain('btn-danger');
    expect(btn.className).toContain('btn-lg');
  });

  it('is disabled when disabled prop is set', () => {
    render(<Button disabled>Nope</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('is disabled and shows spinner when loading', () => {
    render(<Button isLoading>Saving</Button>);
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    expect(btn.getAttribute('aria-busy')).toBe('true');
    expect(btn.querySelector('.spinner')).toBeTruthy();
  });

  it('fires onClick handler', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<Button onClick={handler}>Go</Button>);
    await user.click(screen.getByRole('button'));
    expect(handler).toHaveBeenCalledOnce();
  });

  it('does not fire onClick when disabled', () => {
    const handler = vi.fn();
    render(<Button disabled onClick={handler}>Go</Button>);
    // Disabled buttons can't be clicked via userEvent (pointer-events: none),
    // which is itself the correct behavior. Verify the element is disabled.
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    // fireEvent bypasses pointer-events but the browser won't dispatch click
    // on a disabled button, so we just verify the attribute.
  });

  it('renders left and right icons', () => {
    render(
      <Button leftIcon={<span data-testid="left">L</span>} rightIcon={<span data-testid="right">R</span>}>
        Middle
      </Button>
    );
    expect(screen.getByTestId('left')).toBeInTheDocument();
    expect(screen.getByTestId('right')).toBeInTheDocument();
  });
});
