import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import { MapStep } from '../wizard/MapStep';
import type { ColumnMapping } from '../types';

const mapping = (over: Partial<ColumnMapping> = {}): ColumnMapping => ({ treatment: '', outcome: '', confounders: [], ...over });

// BUG-141: errors and toggle state were visual-only.
describe('MapStep accessibility', () => {
  it('marks an invalid select and points aria-describedby at its error', () => {
    render(<MapStep columns={['a', 'b']} mapping={mapping()} errors={{ treatment: 'Pick a treatment' }} onChange={vi.fn()} />);
    const sel = screen.getByTestId('map-treatment');
    expect(sel).toHaveAttribute('aria-invalid', 'true');
    expect(sel).toHaveAccessibleDescription('Pick a treatment');
    expect(screen.getByRole('alert')).toHaveTextContent('Pick a treatment');
    expect(screen.getByTestId('map-outcome')).toHaveAttribute('aria-invalid', 'false');
  });

  it('describes a select by its note when there is no error', () => {
    render(<MapStep columns={['a']} mapping={mapping()} errors={{}} notes={{ outcome: 'Looks numeric' }} onChange={vi.fn()} />);
    expect(screen.getByTestId('map-outcome')).toHaveAccessibleDescription('Looks numeric');
  });

  it('exposes confounder toggle state and the group error', () => {
    render(<MapStep columns={['a', 'b']} mapping={mapping({ confounders: ['a'] })} errors={{ confounders: 'Too many' }} onChange={vi.fn()} />);
    expect(screen.getByTestId('confounder-a')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('confounder-b')).toHaveAttribute('aria-pressed', 'false');
    const group = screen.getByRole('group', { name: 'Confounders' });
    expect(group).toHaveAccessibleDescription('Too many');
  });
});
