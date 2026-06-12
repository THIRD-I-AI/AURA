import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Stepper } from '../Stepper';

describe('ui/Stepper', () => {
  it('marks done/current/todo states and exposes progress to AT', () => {
    render(<Stepper steps={['Upload', 'Map', 'Review']} current={1} />);
    const list = screen.getByRole('list');
    expect(list.className).toContain('ui-stepper');
    expect(screen.getByText('Upload').closest('li')!.className).toContain('ui-step--done');
    const current = screen.getByText('Map').closest('li')!;
    expect(current.className).toContain('ui-step--current');
    expect(current.getAttribute('aria-current')).toBe('step');
    expect(screen.getByText('Review').closest('li')!.className).toContain('ui-step--todo');
  });
});
