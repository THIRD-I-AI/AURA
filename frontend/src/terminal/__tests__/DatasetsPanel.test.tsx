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
    render(<DatasetsPanel api={{} as never} params={{} as never} containerApi={{} as never} group={{} as never} />);
    await waitFor(() => expect(screen.getByText('sales.csv')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('dataset-row-sales.csv'));
    expect(setActiveDataset).toHaveBeenCalledWith('sales.csv');
  });
});
