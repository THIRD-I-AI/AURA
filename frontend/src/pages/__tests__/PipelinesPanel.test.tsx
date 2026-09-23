import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  etlService: {
    listSourceFiles: vi.fn().mockResolvedValue([]),
    previewSource: vi.fn().mockResolvedValue(null),
    execute: vi.fn().mockResolvedValue({}),
    getDownloadUrl: vi.fn(),
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
});
