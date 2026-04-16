import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ProgressBar } from '../ProgressBar';

describe('ProgressBar', () => {
  it('renders a progressbar role', () => {
    render(<ProgressBar value={50} />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('sets aria-valuenow from value', () => {
    render(<ProgressBar value={75} />);
    const bar = screen.getByRole('progressbar');
    expect(bar.getAttribute('aria-valuenow')).toBe('75');
  });

  it('normalizes 0-1 range to 0-100', () => {
    render(<ProgressBar value={0.5} />);
    const bar = screen.getByRole('progressbar');
    expect(bar.getAttribute('aria-valuenow')).toBe('50');
  });

  it('clamps value to 0-100', () => {
    render(<ProgressBar value={150} />);
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('100');
  });

  it('shows label text', () => {
    render(<ProgressBar value={60} label="Uploading..." />);
    expect(screen.getByText('Uploading...')).toBeInTheDocument();
  });

  it('shows percent text when showPercent is true', () => {
    render(<ProgressBar value={42} showPercent />);
    expect(screen.getByText('42%')).toBeInTheDocument();
  });

  it('does not set aria-valuenow when indeterminate', () => {
    render(<ProgressBar indeterminate />);
    const bar = screen.getByRole('progressbar');
    expect(bar.getAttribute('aria-valuenow')).toBeNull();
  });
});
