import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const getUploadedFiles = vi.fn();
vi.mock('../../services/api', () => ({ uploadService: { getUploadedFiles: () => getUploadedFiles() } }));
const setActiveDataset = vi.fn();
vi.mock('../CockpitProvider', () => ({ useCockpit: () => ({ activeDataset: null, setActiveDataset }) }));

import DatasetsPanel from '../panels/DatasetsPanel';

describe('DatasetsPanel', () => {
  it('lists datasets and sets the active dataset on row click', async () => {
    getUploadedFiles.mockResolvedValue([
      { filename: 'sales.csv', size: 10, modified: 'now' },
      { filename: 'orders.csv', size: 20, modified: 'now' },
    ]);
    render(<DatasetsPanel api={{} as never} params={{} as never} containerApi={{} as never} />);
    await waitFor(() => expect(screen.getByText('sales.csv')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('dataset-row-sales.csv'));
    expect(setActiveDataset).toHaveBeenCalledWith('sales.csv');
  });

  // BUG-256: rows were <tr onClick> only, so the keyboard could not pick the
  // dataset the Query panel filters on.
  it('a row can be focused and selected from the keyboard', async () => {
    setActiveDataset.mockClear();
    getUploadedFiles.mockResolvedValue([{ filename: 'orders.csv', size: 20, modified: 'now' }]);
    render(<DatasetsPanel api={{} as never} params={{} as never} containerApi={{} as never} />);
    const row = await screen.findByTestId('dataset-row-orders.csv');
    expect(row).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(row, { key: 'Enter' });
    expect(setActiveDataset).toHaveBeenCalledWith('orders.csv');
    setActiveDataset.mockClear();
    fireEvent.keyDown(row, { key: ' ' });
    expect(setActiveDataset).toHaveBeenCalledWith('orders.csv');
  });
});
