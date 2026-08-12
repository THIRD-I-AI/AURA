import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  metadataService: {
    listModels: vi.fn(),
  },
}));

import { metadataService } from '../../../services/api';
import MetadataStorePanel from '../MetadataStorePanel';

const listModels = metadataService.listModels as ReturnType<typeof vi.fn>;

describe('MetadataStorePanel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders real catalog models and their real field schema from GET /semantic/models', async () => {
    listModels.mockResolvedValue([
      {
        id: 'm1', name: 'orders', description: 'Order facts', source: {}, tags: ['sales'],
        fields: [{ id: 'f1', name: 'revenue', field_type: 'measure', data_type: 'float' }],
      },
    ]);
    render(<MetadataStorePanel />);
    await waitFor(() => expect(screen.getByText('orders')).toBeInTheDocument());
    expect(screen.getByText('Order facts')).toBeInTheDocument();
    expect(screen.getByText('sales')).toBeInTheDocument();
    expect(screen.getByText('revenue:float')).toBeInTheDocument();
  });

  it('renders an honest empty state when the catalog has no models', async () => {
    listModels.mockResolvedValue([]);
    render(<MetadataStorePanel />);
    await waitFor(() => expect(screen.getByText(/no catalog models yet/i)).toBeInTheDocument());
  });

  it('renders an honest error state when the metadata store is unreachable', async () => {
    listModels.mockRejectedValue(new Error('Metadata store is unavailable.'));
    render(<MetadataStorePanel />);
    await waitFor(() => expect(screen.getByText(/metadata store is unavailable/i)).toBeInTheDocument());
  });
});
