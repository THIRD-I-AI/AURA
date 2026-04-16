import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  healthService: {
    checkHealth: vi.fn().mockResolvedValue({ status: 'healthy', services: {} }),
  },
}));

import Settings from '../Settings';

describe('Settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();

    // Mock window.matchMedia for the Appearance section
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it('renders the API Connection section', () => {
    render(<Settings />);
    expect(screen.getByText('API Connection')).toBeInTheDocument();
  });

  it('renders the Appearance section', () => {
    render(<Settings />);
    expect(screen.getByText('Appearance')).toBeInTheDocument();
  });

  it('renders the System section', () => {
    render(<Settings />);
    expect(screen.getByText('System')).toBeInTheDocument();
  });

  it('renders the Danger Zone section', () => {
    render(<Settings />);
    expect(screen.getByText('Danger Zone')).toBeInTheDocument();
  });

  it('has a Test Connection button', () => {
    render(<Settings />);
    expect(screen.getByText('Test')).toBeInTheDocument();
  });

  it('has a Save Changes button', () => {
    render(<Settings />);
    expect(screen.getByText('Save Changes')).toBeInTheDocument();
  });
});
