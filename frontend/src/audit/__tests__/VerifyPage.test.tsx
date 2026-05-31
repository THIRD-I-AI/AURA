import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import { VerifyPage } from '../VerifyPage';
import { auditApi } from '../auditApi';

function renderAt(hash: string) {
  return render(
    <MemoryRouter initialEntries={[`/verify/${hash}`]}>
      <Routes><Route path="/verify/:hash" element={<VerifyPage />} /></Routes>
    </MemoryRouter>,
  );
}

describe('VerifyPage', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('shows the verified certificate for a good hash', async () => {
    vi.spyOn(auditApi, 'verify').mockResolvedValue({ record_hash: 'good', verified: true, signature_status: 'signed', signing_key_source: 'persisted_file' });
    vi.spyOn(auditApi, 'getArtifact').mockResolvedValue({ audit_record_hash: 'good', estimates: [], refutations: [], signature_status: 'signed', signing_key_source: 'persisted_file' });
    renderAt('good');
    await waitFor(() => expect(screen.getByTestId('cert-verify-status')).toHaveTextContent(/verified/i));
  });

  it('shows an explicit NOT-verified state for an invalid hash', async () => {
    vi.spyOn(auditApi, 'verify').mockResolvedValue({ record_hash: 'bad', verified: false, signature_status: 'unknown', signing_key_source: 'none', reason: 'unknown artifact' });
    vi.spyOn(auditApi, 'getArtifact').mockResolvedValue({ audit_record_hash: 'bad', estimates: [], refutations: [], signature_status: 'unknown', signing_key_source: 'none' });
    renderAt('bad');
    await waitFor(() => expect(screen.getByTestId('cert-verify-status')).toHaveTextContent(/not verified/i));
    expect(screen.getByTestId('cert-verify-status')).toHaveTextContent('unknown artifact');
  });
});
