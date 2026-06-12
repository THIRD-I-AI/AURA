import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Card } from '../Card';

describe('ui/Card', () => {
  it('renders header/body composition with classes only', () => {
    render(
      <Card title="Service health" subtitle="live">
        <p>body content</p>
      </Card>,
    );
    expect(screen.getByRole('heading', { name: 'Service health' })).toBeInTheDocument();
    expect(screen.getByText('live')).toBeInTheDocument();
    const card = screen.getByText('body content').closest('.ui-card');
    expect(card).not.toBeNull();
  });

  it('supports an evidence accent edge', () => {
    render(<Card accent="warn">flagged</Card>);
    expect(screen.getByText('flagged').closest('.ui-card')!.className).toContain('ui-card--warn');
  });
});
