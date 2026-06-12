import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Button } from '../Button';

describe('ui/Button', () => {
  it('renders variants as classes (CSS-only styling — CSP path)', () => {
    render(<Button variant="danger">Revoke</Button>);
    const btn = screen.getByRole('button', { name: 'Revoke' });
    expect(btn.className).toContain('ui-btn');
    expect(btn.className).toContain('ui-btn--danger');
    expect(btn.getAttribute('style')).toBeNull();
  });

  it('disabled keeps a visible affordance class and blocks clicks', () => {
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Next</Button>);
    const btn = screen.getByRole('button', { name: 'Next' });
    expect(btn).toBeDisabled();
    btn.click();
    expect(onClick).not.toHaveBeenCalled();
  });

  it('defaults to primary, supports size', () => {
    render(<Button size="sm">Go</Button>);
    const btn = screen.getByRole('button', { name: 'Go' });
    expect(btn.className).toContain('ui-btn--primary');
    expect(btn.className).toContain('ui-btn--sm');
  });
});
