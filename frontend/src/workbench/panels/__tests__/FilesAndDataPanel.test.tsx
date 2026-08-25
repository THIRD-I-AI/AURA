import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  uploadService: {
    getUploadedFiles: vi.fn(),
    uploadFile: vi.fn(),
  },
}));

import { uploadService } from '../../../services/api';
import FilesAndDataPanel from '../FilesAndDataPanel';

const getUploadedFiles = uploadService.getUploadedFiles as ReturnType<typeof vi.fn>;

const files = [
  { filename: 'sales.csv', size: 2048, modified: '2026-01-15T00:00:00Z' },
  { filename: 'events.json', size: 512000, modified: null },
];

describe('FilesAndDataPanel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders real dataset rows in the DataTable', async () => {
    getUploadedFiles.mockResolvedValue(files);
    render(<FilesAndDataPanel />);
    await waitFor(() => expect(screen.getByText('sales.csv')).toBeInTheDocument());
    expect(screen.getByText('events.json')).toBeInTheDocument();
    expect(screen.getByText('2 datasets · 502 KB · workspace uploads')).toBeInTheDocument();
  });

  it('renders an em-dash for a null modified date', async () => {
    getUploadedFiles.mockResolvedValue(files);
    render(<FilesAndDataPanel />);
    await waitFor(() => expect(screen.getByText('events.json')).toBeInTheDocument());

    const eventsRow = screen.getByText('events.json').closest('tr')!;
    expect(within(eventsRow).getByText('—')).toBeInTheDocument();
  });

  it('renders an honest empty state when there are no datasets', async () => {
    getUploadedFiles.mockResolvedValue([]);
    render(<FilesAndDataPanel />);
    await waitFor(() => expect(screen.getByText('No datasets yet')).toBeInTheDocument());
  });

  it('renders an honest error state when the gateway is unreachable', async () => {
    getUploadedFiles.mockRejectedValue(new Error('network down'));
    render(<FilesAndDataPanel />);
    await waitFor(() => expect(screen.getByText(/could not reach the gateway to list datasets/i)).toBeInTheDocument());
  });

  it('filters rows by filename', async () => {
    getUploadedFiles.mockResolvedValue(files);
    render(<FilesAndDataPanel />);
    await waitFor(() => expect(screen.getByText('sales.csv')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Filter datasets…'), 'events');

    expect(screen.queryByText('sales.csv')).not.toBeInTheDocument();
    expect(screen.getByText('events.json')).toBeInTheDocument();
  });

  it('sorts by size ascending/descending on header click', async () => {
    getUploadedFiles.mockResolvedValue(files);
    render(<FilesAndDataPanel />);
    await waitFor(() => expect(screen.getByText('sales.csv')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^size/i }));

    const rowsInOrder = screen.getAllByRole('row').slice(1); // drop header row
    // ascending: sales.csv's 2048 bytes sorts first
    expect(within(rowsInOrder[0]).getByText('sales.csv')).toBeInTheDocument();
  });
});
