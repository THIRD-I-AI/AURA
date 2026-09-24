import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { auditApi } from '../auditApi';
import { LedgerProof } from '../LedgerProof';
import type { LedgerProof as LedgerProofData } from '../types';
import vectors from './merkleVectors.json';

// A REAL proof produced by the backend's shared.merkle, so "verified" below is a
// genuine browser-side recomputation and not a mocked flag.
const V = (vectors as Array<{ tree_size: number; record_hashes: string[]; root: string; proofs: string[][] }>)
  .find((x) => x.tree_size === 9)!;
const CERT = 'c'.repeat(64);
const good = (): LedgerProofData => ({
  tenant_id: 't1',
  tree_size: V.tree_size,
  leaf_index: 6,
  cert_hash: CERT,
  record_hash: V.record_hashes[6],
  proof_hex: [...V.proofs[6]],
  root_hash_hex: V.root,
});

const check = async () => userEvent.click(screen.getByTestId('ledger-proof-check'));

describe('LedgerProof', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('does nothing until asked — no request on mount', () => {
    const spy = vi.spyOn(auditApi, 'ledgerProof');
    render(<LedgerProof certHash={CERT} />);
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByText(/re-hashed in your browser/i)).toBeInTheDocument();
  });

  it('fetches the proof for this certificate, recomputes it, and shows the root with an honest caveat', async () => {
    const spy = vi.spyOn(auditApi, 'ledgerProof').mockResolvedValue(good());
    render(<LedgerProof certHash={CERT} />);
    await check();

    const result = await screen.findByTestId('ledger-proof-result');
    expect(spy).toHaveBeenCalledWith(CERT);
    expect(result).toHaveAttribute('role', 'status');
    expect(result).toHaveTextContent(/verified in this browser/i);
    expect(screen.getByTestId('ledger-proof-root')).toHaveTextContent(V.root);
    // Says what it does NOT prove.
    expect(screen.getByTestId('ledger-proof')).toHaveTextContent(/compare it with a root you pinned/i);
    expect(screen.getByTestId('ledger-proof')).toHaveTextContent(/tree size is reported by the server/i);
  });

  it('flags a proof that does not recompute as an alert, never as verified', async () => {
    const bad = good();
    bad.proof_hex[0] = (bad.proof_hex[0][0] === '0' ? '1' : '0') + bad.proof_hex[0].slice(1);
    vi.spyOn(auditApi, 'ledgerProof').mockResolvedValue(bad);
    render(<LedgerProof certHash={CERT} />);
    await check();

    const result = await screen.findByTestId('ledger-proof-result');
    expect(result).toHaveAttribute('role', 'alert');
    expect(result).toHaveTextContent(/does not verify/i);
    expect(screen.queryByText(/verified in this browser/i)).not.toBeInTheDocument();
  });

  it('refuses a proof the server returned for a different certificate', async () => {
    vi.spyOn(auditApi, 'ledgerProof').mockResolvedValue({ ...good(), cert_hash: 'd'.repeat(64) });
    render(<LedgerProof certHash={CERT} />);
    await check();
    expect(await screen.findByTestId('ledger-proof-result')).toHaveTextContent(/different certificate/i);
    expect(screen.queryByTestId('ledger-proof-detail')).not.toBeInTheDocument();
  });

  it.each([
    ['a 404', 'HTTP 404: {"detail":"no ledger record certifies that hash"}', /no ledger record certifies this hash/i, 'note'],
    ['a 401', 'HTTP 401: unauthorized', /sign in/i, 'alert'],
    ['a 403', 'HTTP 403: forbidden', /sign in/i, 'alert'],
    ['a server error', 'HTTP 500: boom', /could not check inclusion: HTTP 500/i, 'alert'],
  ])('handles %s', async (_name, message, text, role) => {
    vi.spyOn(auditApi, 'ledgerProof').mockRejectedValue(new Error(message));
    render(<LedgerProof certHash={CERT} />);
    await check();
    const result = await screen.findByTestId('ledger-proof-result');
    expect(result).toHaveTextContent(text);
    expect(result).toHaveAttribute('role', role);
  });

  it('reports a browser that cannot hash instead of crashing', async () => {
    vi.spyOn(auditApi, 'ledgerProof').mockResolvedValue(good());
    vi.stubGlobal('crypto', {});
    render(<LedgerProof certHash={CERT} />);
    await check();
    expect(await screen.findByTestId('ledger-proof-result')).toHaveTextContent(/cannot hash/i);
  });

  it('disables the button while checking', async () => {
    let release: (p: LedgerProofData) => void = () => {};
    vi.spyOn(auditApi, 'ledgerProof').mockReturnValue(new Promise((r) => { release = r; }));
    render(<LedgerProof certHash={CERT} />);
    await check();
    expect(screen.getByTestId('ledger-proof-check')).toBeDisabled();
    expect(screen.getByTestId('ledger-proof-check')).toHaveTextContent(/checking/i);
    release(good());
    expect(await screen.findByTestId('ledger-proof-result')).toBeInTheDocument();
    expect(screen.getByTestId('ledger-proof-check')).toBeEnabled();
  });
});
