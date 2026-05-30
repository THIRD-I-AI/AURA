import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { Certificate } from '../Certificate';
import type { Artifact } from '../types';

const artifact: Artifact = {
  audit_record_hash: 'a3f9c1deadbeef',
  estimates: [{ method: 'tmle', point_estimate: 0.08, ci_low: 0.01, ci_high: 0.15 }],
  refutations: [],
  signature_status: 'signed',
  signing_key_source: 'persisted_file',
};

describe('Certificate', () => {
  it('shows the hash, signature badge and key source', () => {
    render(<MemoryRouter><Certificate artifact={artifact} /></MemoryRouter>);
    expect(screen.getByTestId('cert-hash')).toHaveTextContent('a3f9c1deadbeef');
    expect(screen.getByTestId('cert-signature-badge')).toHaveTextContent(/signed/i);
    expect(screen.getByTestId('cert-key-source')).toHaveTextContent('persisted_file');
  });

  it('renders PDF + verify actions by default', () => {
    render(<MemoryRouter><Certificate artifact={artifact} /></MemoryRouter>);
    expect(screen.getByTestId('cert-download-pdf')).toBeInTheDocument();
    expect(screen.getByTestId('cert-verify-link')).toBeInTheDocument();
  });

  it('hides actions in readOnly mode (used by the public verify page)', () => {
    render(<MemoryRouter><Certificate artifact={artifact} readOnly /></MemoryRouter>);
    expect(screen.queryByTestId('cert-download-pdf')).not.toBeInTheDocument();
  });

  it('shows NOT-verified state when verifyResult says so', () => {
    render(<MemoryRouter><Certificate artifact={artifact} readOnly verifyResult={{ record_hash: 'a3f9c1deadbeef', verified: false, signature_status: 'bad', signing_key_source: 'persisted_file', reason: 'signature mismatch' }} /></MemoryRouter>);
    expect(screen.getByTestId('cert-verify-status')).toHaveTextContent(/not verified/i);
    expect(screen.getByTestId('cert-verify-status')).toHaveTextContent('signature mismatch');
  });
});
