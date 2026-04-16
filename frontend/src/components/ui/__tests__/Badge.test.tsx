import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Badge } from '../Badge';

describe('Badge', () => {
  it('renders children', () => {
    render(<Badge>Active</Badge>);
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('maps legacy color prop to variant class', () => {
    const { container } = render(<Badge color="success">OK</Badge>);
    expect(container.querySelector('.badge-green')).toBeTruthy();
  });

  it('prefers variant over color', () => {
    const { container } = render(<Badge color="success" variant="red">Err</Badge>);
    expect(container.querySelector('.badge-red')).toBeTruthy();
  });

  it('shows pulse dot when live', () => {
    const { container } = render(<Badge live>Live</Badge>);
    expect(container.querySelector('.badge-dot--pulse')).toBeTruthy();
  });

  it('renders icon when not live', () => {
    render(<Badge icon={<span data-testid="ico">★</span>}>Star</Badge>);
    expect(screen.getByTestId('ico')).toBeInTheDocument();
  });
});
