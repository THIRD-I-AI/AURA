/* "Ledger inclusion proof" for one certificate (BUG-159). Fetches the tenant's
   RFC 6962 Merkle proof and RECOMPUTES the root in the browser (audit/merkle.ts)
   rather than trusting a server-side "ok".

   What it does and does not establish, stated on screen: a valid proof shows the
   displayed root commits to this record at this position. It does not prove the
   root itself is the one you should trust — that needs comparing it with a root
   you pinned or published earlier — and it does not prove the tree size. */
import { useState } from 'react';

import { Button } from '@/components/ui-kit/button';
import { cn } from '@/lib/cn';
import { auditApi } from './auditApi';
import { verifyInclusion } from './merkle';
import type { LedgerProof as LedgerProofData } from './types';

type State =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'verified'; proof: LedgerProofData }
  | { kind: 'invalid'; proof: LedgerProofData }
  | { kind: 'wrong-cert' }
  | { kind: 'not-chained' }
  | { kind: 'signed-out' }
  | { kind: 'error'; message: string };

const ROW = 'flex flex-col gap-0.5 sm:flex-row sm:gap-3';
const LABEL = 'w-28 shrink-0 font-mono text-2xs uppercase tracking-wider text-text-tertiary';
const VALUE = 'min-w-0 break-all font-mono text-xs text-text-primary';

export function LedgerProof({ certHash }: { certHash: string }) {
  const [state, setState] = useState<State>({ kind: 'idle' });

  const check = async () => {
    setState({ kind: 'loading' });
    try {
      const proof = await auditApi.ledgerProof(certHash);
      // The proof is for whatever the server looked up; make sure it is for THIS
      // certificate before presenting it as evidence about it.
      if (proof.cert_hash !== certHash) { setState({ kind: 'wrong-cert' }); return; }
      const ok = await verifyInclusion({
        record_hash: proof.record_hash,
        leaf_index: proof.leaf_index,
        tree_size: proof.tree_size,
        proof_hex: proof.proof_hex,
        root_hash_hex: proof.root_hash_hex,
      });
      setState({ kind: ok ? 'verified' : 'invalid', proof });
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (/HTTP 404/.test(message)) setState({ kind: 'not-chained' });
      else if (/HTTP 40[13]/.test(message)) setState({ kind: 'signed-out' });
      else setState({ kind: 'error', message });
    }
  };

  const proof = state.kind === 'verified' || state.kind === 'invalid' ? state.proof : null;

  return (
    <section data-testid="ledger-proof" className="mt-4 flex flex-col gap-2 border border-border-hairline p-3">
      <div className="flex items-center gap-3">
        <h2 className="font-mono text-2xs font-semibold uppercase tracking-widest text-text-secondary">Ledger inclusion proof</h2>
        <div className="flex-1" />
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={check}
          disabled={state.kind === 'loading'}
          data-testid="ledger-proof-check"
        >
          {state.kind === 'loading' ? 'Checking…' : 'Check ledger inclusion'}
        </Button>
      </div>

      {state.kind === 'idle' && (
        <p className="text-xs leading-snug text-text-tertiary">
          Prove this certificate is part of your tamper-evident audit chain. The proof is fetched and
          re-hashed in your browser — you are not asked to trust a server-side "ok".
        </p>
      )}

      {state.kind === 'verified' && (
        <p role="status" data-testid="ledger-proof-result" className="font-mono text-xs font-semibold text-signal">
          Verified in this browser — the record hash, leaf position and proof path recompute to the root below.
        </p>
      )}
      {state.kind === 'invalid' && (
        <p role="alert" data-testid="ledger-proof-result" className="font-mono text-xs font-semibold text-danger">
          Proof does NOT verify — the server's proof does not recompute to its own root. Treat this record as unverified.
        </p>
      )}
      {state.kind === 'wrong-cert' && (
        <p role="alert" data-testid="ledger-proof-result" className="font-mono text-xs text-danger">
          The server returned a proof for a different certificate than the one on this page, so it was not used.
        </p>
      )}
      {state.kind === 'not-chained' && (
        <p role="note" data-testid="ledger-proof-result" className="font-mono text-xs text-warn">
          No ledger record certifies this hash in your tenant chain, so there is no inclusion proof to check.
        </p>
      )}
      {state.kind === 'signed-out' && (
        <p role="alert" data-testid="ledger-proof-result" className="font-mono text-xs text-warn">
          Sign in to check ledger inclusion — proofs are scoped to your organisation's chain.
        </p>
      )}
      {state.kind === 'error' && (
        <p role="alert" data-testid="ledger-proof-result" className="font-mono text-xs text-danger">
          Could not check inclusion: {state.message}
        </p>
      )}

      {proof && (
        <div className="flex flex-col gap-2">
          <dl data-testid="ledger-proof-detail" className={cn('flex flex-col gap-1.5', state.kind === 'invalid' && 'opacity-70')}>
            <div className={ROW}><dt className={LABEL}>Record hash</dt><dd className={VALUE}>{proof.record_hash}</dd></div>
            <div className={ROW}><dt className={LABEL}>Position</dt><dd className={VALUE}>leaf {proof.leaf_index} (server reports a tree of {proof.tree_size})</dd></div>
            <div className={ROW}><dt className={LABEL}>Proof path</dt><dd className={VALUE}>{proof.proof_hex.length} sibling hash{proof.proof_hex.length === 1 ? '' : 'es'}</dd></div>
            <div className={ROW}><dt className={LABEL}>Merkle root</dt><dd data-testid="ledger-proof-root" className={VALUE}>{proof.root_hash_hex}</dd></div>
          </dl>
          {state.kind === 'verified' && (
            <p className="text-2xs leading-snug text-text-tertiary">
              This shows the root commits to this record at this position. It does not show the root is the one to trust:
              compare it with a root you pinned or published earlier to be independent of AURA. The tree size is reported
              by the server and is not proven by the path.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
