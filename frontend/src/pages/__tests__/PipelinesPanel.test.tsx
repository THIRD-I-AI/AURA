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

describe('PipelinesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the page heading', () => {
    render(<PipelinesPanel />);
    expect(screen.getByText('Data Pipeline Builder')).toBeInTheDocument();
  });

  it('renders tab buttons', () => {
    render(<PipelinesPanel />);
    expect(screen.getByText('AI Pipeline')).toBeInTheDocument();
    expect(screen.getByText('Visual Builder')).toBeInTheDocument();
    expect(screen.getByText('Saved')).toBeInTheDocument();
  });

  it('renders KPI cards', () => {
    render(<PipelinesPanel />);
    expect(screen.getByText('Source Files')).toBeInTheDocument();
    expect(screen.getByText('Pipeline Steps')).toBeInTheDocument();
  });
});
