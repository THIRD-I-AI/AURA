import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  streamingService: {
    list: vi.fn().mockResolvedValue({ pipelines: [] }),
    templates: vi.fn().mockResolvedValue({ templates: [] }),
    schemas: vi.fn().mockResolvedValue({ sources: {}, sinks: {}, windows: {}, transforms: {} }),
    streamUrl: vi.fn(),
    metrics: vi.fn(),
  },
}));

import StreamingPanel from '../StreamingPanel';

describe('StreamingPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders tab buttons', () => {
    render(<StreamingPanel />);
    expect(screen.getByText('My Pipelines')).toBeInTheDocument();
    expect(screen.getByText('Templates')).toBeInTheDocument();
  });

  it('renders Create tab', () => {
    render(<StreamingPanel />);
    expect(screen.getByText('+ Create')).toBeInTheDocument();
  });

  it('shows pipelines tab by default', () => {
    render(<StreamingPanel />);
    // Should show the pipeline-related content
    expect(screen.getByText('My Pipelines')).toBeInTheDocument();
  });
});
