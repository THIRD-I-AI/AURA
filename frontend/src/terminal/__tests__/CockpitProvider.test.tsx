import { act, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CockpitProvider, useCockpit } from '../CockpitProvider';

function Probe() {
  const { activeDataset, setActiveDataset } = useCockpit();
  return (
    <div>
      <span data-testid="active">{activeDataset ?? 'none'}</span>
      <button onClick={() => setActiveDataset('sales.csv')}>set</button>
    </div>
  );
}

describe('CockpitProvider', () => {
  it('defaults to no active dataset and updates on setActiveDataset', () => {
    render(<CockpitProvider><Probe /></CockpitProvider>);
    expect(screen.getByTestId('active').textContent).toBe('none');
    act(() => { screen.getByText('set').click(); });
    expect(screen.getByTestId('active').textContent).toBe('sales.csv');
  });
});
