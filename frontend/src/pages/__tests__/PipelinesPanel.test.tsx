import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  etlService: {
    listSourceFiles: vi.fn().mockResolvedValue([]),
    previewSource: vi.fn().mockResolvedValue(null),
    execute: vi.fn().mockResolvedValue({}),
    getDownloadUrl: vi.fn(),
  },
  uploadService: {
    getUploadedFiles: vi.fn().mockResolvedValue([]),
  },
  pipelineService: {
    list: vi.fn().mockResolvedValue({ status: 'success', pipelines: [] }),
    get: vi.fn(),
    generate: vi.fn(),
    executeAsync: vi.fn(),
    save: vi.fn(),
    remove: vi.fn(),
    getDownloadUrl: vi.fn(),
  },
}));

// Mock the PipelineMonitor sub-component
vi.mock('../../components/PipelineMonitor', () => ({
  default: () => <div data-testid="pipeline-monitor">Monitor</div>,
  PipelineMonitor: () => <div data-testid="pipeline-monitor">Monitor</div>,
}));

import PipelinesPanel from '../PipelinesPanel';
import { uploadService } from '../../services/api';
import { ToastProvider } from '../../contexts/ToastContext';

// PipelinesPanel reads useToast() (BUG-148: unified onto the shared queue),
// so it needs a real provider in the tree, not a bare render.
function renderPanel() {
  return render(<ToastProvider><PipelinesPanel /></ToastProvider>);
}

describe('PipelinesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the page heading', () => {
    renderPanel();
    expect(screen.getByText('Data Pipeline Builder')).toBeInTheDocument();
  });

  it('renders tab buttons', () => {
    renderPanel();
    expect(screen.getByText('AI Pipeline')).toBeInTheDocument();
    expect(screen.getByText('Visual Builder')).toBeInTheDocument();
    expect(screen.getByText('Saved')).toBeInTheDocument();
  });

  it('renders KPI cards', () => {
    renderPanel();
    expect(screen.getByText('Source Files')).toBeInTheDocument();
    expect(screen.getByText('Pipeline Steps')).toBeInTheDocument();
  });

  // BUG-251: the source-file list came from a bare fetch() with no Authorization
  // header, so a signed-in user's uploads never reached the dropdown.
  it('lists source files through the authenticated client, data files only', async () => {
    const bareFetch = vi.fn();
    globalThis.fetch = bareFetch as unknown as typeof fetch;
    vi.mocked(uploadService.getUploadedFiles).mockResolvedValue([
      { filename: 'orders.csv', size: 1, modified: '' },
      { filename: 'notes.txt', size: 1, modified: '' },
      { filename: 'events.parquet', size: 1, modified: '' },
    ]);
    renderPanel();
    await waitFor(() => expect(screen.getByRole('option', { name: 'orders.csv' })).toBeInTheDocument());
    expect(screen.getByRole('option', { name: 'events.parquet' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'notes.txt' })).not.toBeInTheDocument();
    expect(bareFetch).not.toHaveBeenCalled();
  });

  it('tells the user when the file list cannot be loaded', async () => {
    vi.mocked(uploadService.getUploadedFiles).mockRejectedValue(new Error('HTTP 500'));
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load your uploaded files');
  });
});
