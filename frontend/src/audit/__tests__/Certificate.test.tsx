import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { Certificate } from '../Certificate';
import type { Artifact } from '../types';

const artifact: Artifact = {
  audit_record_hash: 'a3f9c1deadbeef',
  estimates: [{ method: 'tmle', point: 0.08, ci_lower: 0.01, ci_upper: 0.15 }],
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

  it('renders the certificate inside the light certificate theme island', () => {
    render(<MemoryRouter><Certificate artifact={artifact} /></MemoryRouter>);
    expect(screen.getByTestId('certificate').closest('[data-theme="certificate"]')).not.toBeNull();
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

  it('always offers a way back (dashboard + run-another) — never a dead end', () => {
    const { rerender } = render(<MemoryRouter><Certificate artifact={artifact} /></MemoryRouter>);
    expect(screen.getByTestId('cert-back-dashboard')).toBeInTheDocument();
    expect(screen.getByTestId('cert-run-another')).toBeInTheDocument();
    // the read-only public verify page must also have nav (regression: it had none)
    rerender(<MemoryRouter><Certificate artifact={artifact} readOnly /></MemoryRouter>);
    expect(screen.getByTestId('cert-back-dashboard')).toBeInTheDocument();
    expect(screen.getByTestId('cert-run-another')).toBeInTheDocument();
  });

  it('shows NOT-verified state when verifyResult says so', () => {
    render(<MemoryRouter><Certificate artifact={artifact} readOnly verifyResult={{ record_hash: 'a3f9c1deadbeef', verified: false, signature_status: 'bad', signing_key_source: 'persisted_file', reason: 'signature mismatch' }} /></MemoryRouter>);
    expect(screen.getByTestId('cert-verify-status')).toHaveTextContent(/not verified/i);
    expect(screen.getByTestId('cert-verify-status')).toHaveTextContent('signature mismatch');
  });

  it('does not trust the self-reported status on a read-only public surface without a verifyResult', () => {
    render(<MemoryRouter><Certificate artifact={artifact} readOnly /></MemoryRouter>);
    // signature_status is 'signed' but with no server-recomputed verifyResult
    // the public badge must stay neutral, not claim "ED25519 signed".
    expect(screen.getByTestId('cert-signature-badge')).not.toHaveTextContent(/ED25519 signed/i);
    expect(screen.getByTestId('cert-signature-badge')).toHaveTextContent(/not independently verified/i);
  });

  it('notes a degraded (cached fail-safe) artifact', () => {
    render(<MemoryRouter><Certificate artifact={{ ...artifact, degraded: true }} /></MemoryRouter>);
    expect(screen.getByTestId('cert-degraded')).toHaveTextContent(/cached result/i);
  });

  it('derives a verdict from string-typed estimates (replay path) and skips errored estimators', () => {
    // Replay numbers are strings; an errored estimator must not skew the verdict.
    const replay: Artifact = {
      ...artifact,
      estimates: [
        { method: 'double_ml', point: '0.084', ci_lower: '0.02', ci_upper: '0.15' },
        { method: 'iv', point: 0, ci_lower: 0, ci_upper: 0, error: 'instrument unavailable' },
      ],
    };
    render(<MemoryRouter><Certificate artifact={replay} /></MemoryRouter>);
    // 0.084 (string) is material (>=0.02); errored iv is ignored, not averaged in as 0.
    expect(screen.getByTestId('certificate')).toHaveTextContent(/disparate impact detected/i);
  });

  it('does NOT claim impact when the point is material but CIs cross zero (e.g. COMPAS)', () => {
    // Adjusted COMPAS: point ~ -0.024 (material) but 95% CIs include zero → not significant.
    const compas: Artifact = {
      ...artifact,
      estimates: [
        { method: 'double_ml', point: -0.024, ci_lower: -0.077, ci_upper: 0.029 },
        { method: 'tmle', point: -0.023, ci_lower: -0.051, ci_upper: 0.005 },
      ],
    };
    render(<MemoryRouter><Certificate artifact={compas} /></MemoryRouter>);
    expect(screen.getByTestId('certificate')).toHaveTextContent(/not statistically significant/i);
    expect(screen.getByTestId('certificate')).not.toHaveTextContent(/disparate impact detected/i);
  });

  it('prefers the backend signed verdict (rendered.verdict.label) over the local rule', () => {
    // Local rule on these estimates would say "detected" (material, CI excludes 0);
    // the backend verdict must win so the web cert matches the signed PDF.
    const withRendered: Artifact = {
      ...artifact,
      estimates: [{ method: 'tmle', point: 0.08, ci_lower: 0.01, ci_upper: 0.15 }],
      rendered: { verdict: { status: 'not_significant', label: 'Server verdict: not statistically significant after adjustment.' } },
    };
    render(<MemoryRouter><Certificate artifact={withRendered} /></MemoryRouter>);
    expect(screen.getByTestId('certificate')).toHaveTextContent('Server verdict: not statistically significant after adjustment.');
    expect(screen.getByTestId('certificate')).not.toHaveTextContent(/disparate impact detected/i);
  });

  it('falls back to the local verdict when the artifact has no rendered block (verify page)', () => {
    // `artifact` has no `rendered`; its tmle 0.08 [0.01,0.15] is material + significant.
    render(<MemoryRouter><Certificate artifact={artifact} /></MemoryRouter>);
    expect(screen.getByTestId('certificate')).toHaveTextContent(/disparate impact detected/i);
  });
});
