import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

// Certificate.tsx always renders a react-router <Link> in its back-nav
// (even readOnly) — same requirement VerifyPage's own test has.
import { auditApi } from '../../../audit/auditApi';
import CertificatesPanel from '../CertificatesPanel';

function renderPanel() {
  return render(
    <MemoryRouter>
      <CertificatesPanel />
    </MemoryRouter>,
  );
}

describe('CertificatesPanel', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('shows an honest awaiting state before any lookup', () => {
    renderPanel();
    expect(screen.getByText(/no certificate looked up/i)).toBeInTheDocument();
  });

  it('rejects a malformed hash locally without hitting the network', async () => {
    const verifySpy = vi.spyOn(auditApi, 'verify');
    renderPanel();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/certificate record hash/i), 'not-a-hash');
    await user.click(screen.getByRole('button', { name: /verify/i }));
    expect(screen.getByText(/64-character record hash/i)).toBeInTheDocument();
    expect(verifySpy).not.toHaveBeenCalled();
  });

  it('renders the real verified certificate for a good hash, reusing the shared Certificate component', async () => {
    const hash = 'a'.repeat(64);
    vi.spyOn(auditApi, 'verify').mockResolvedValue({
      record_hash: hash, verified: true, signature_status: 'signed', signing_key_source: 'persisted_file',
    });
    vi.spyOn(auditApi, 'getArtifact').mockResolvedValue({
      audit_record_hash: hash, estimates: [], refutations: [], signature_status: 'signed', signing_key_source: 'persisted_file',
    });
    renderPanel();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/certificate record hash/i), hash);
    await user.click(screen.getByRole('button', { name: /verify/i }));
    await waitFor(() => expect(screen.getByTestId('wb-certificate-result')).toBeInTheDocument());
    expect(screen.getByTestId('cert-verify-status')).toHaveTextContent(/verified/i);
    expect(screen.getByTestId('cert-hash')).toHaveTextContent(hash);
  });

  it('shows an honest error state (the real backend message) when verification fails', async () => {
    const hash = 'b'.repeat(64);
    vi.spyOn(auditApi, 'verify').mockRejectedValue(new Error('HTTP 404: unknown record'));
    renderPanel();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/certificate record hash/i), hash);
    await user.click(screen.getByRole('button', { name: /verify/i }));
    await waitFor(() => expect(screen.getByText(/verification failed/i)).toBeInTheDocument());
    expect(screen.getByText(/HTTP 404: unknown record/)).toBeInTheDocument();
  });
});
