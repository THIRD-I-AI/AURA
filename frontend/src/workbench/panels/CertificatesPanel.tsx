/* Certificates — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md):
   ui-kit primitives + token utilities, no inline styles. No "list my
   certificates" endpoint exists anywhere in counterfactual.py (S31b/S33 never
   shipped one) — this is a lookup surface: enter a record hash and verify it
   through the SAME auditApi.verify()/getArtifact() the public /verify/:hash
   route uses, rendering the shared, presentational Certificate component
   read-only so this panel can never drift from (or duplicate) the public
   verification surface. */
import { useCallback, useState } from 'react';
import { ShieldCheck } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { sanitizeRecordHash } from '../../services/api';
import { auditApi } from '../../audit/auditApi';
import { Certificate } from '../../audit/Certificate';
import type { Artifact, VerifyResult } from '../../audit/types';

type Status = 'idle' | 'loading' | 'error';

export default function CertificatesPanel() {
  const [input, setInput] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState<string | null>(null);
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);

  const verify = useCallback(async () => {
    const hash = sanitizeRecordHash(input.trim().toLowerCase());
    if (!hash) {
      setStatus('error');
      setError('Enter a 64-character record hash (sha256 hex).');
      setVerifyResult(null);
      setArtifact(null);
      return;
    }
    setStatus('loading');
    setError(null);
    try {
      const result = await auditApi.verify(hash);
      setVerifyResult(result);
      // Best-effort richer detail (estimates, rendered verdict) for display —
      // the certificate still renders correctly from verifyResult alone if
      // this 404s (e.g. a financial-audit hash with no artifact-store row).
      const full = await auditApi.getArtifact(hash).catch(() => null);
      setArtifact(full);
      setStatus('idle');
    } catch (e) {
      setStatus('error');
      setError(e instanceof Error ? e.message : 'Could not reach the gateway to verify that hash.');
      setVerifyResult(null);
      setArtifact(null);
    }
  }, [input]);

  // VerifyPage's own fallback shape when the artifact body isn't available —
  // duplicated here (not imported) because it's a 5-line literal, not logic.
  const shown: Artifact | null = verifyResult
    ? artifact ?? {
        audit_record_hash: verifyResult.record_hash,
        estimates: [],
        refutations: [],
        signature_status: verifyResult.signature_status,
        signing_key_source: verifyResult.signing_key_source,
      }
    : null;

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-certificates-panel">
      <p className="font-mono text-2xs text-text-tertiary">
        ED25519-signed audit certificates · verify any record hash independently — no self-reported status trusted
      </p>

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') verify(); }}
          placeholder="Record hash (sha256 hex, 64 chars)"
          aria-label="Certificate record hash"
          className={cn(
            'flex-1 rounded-none border border-border bg-secondary px-3.5 py-2 font-mono text-sm text-card-foreground',
            'placeholder:text-text-tertiary outline-none focus-visible:border-ring',
          )}
        />
        <Button size="sm" onClick={verify} disabled={status === 'loading' || !input.trim()} className="px-4">
          {status === 'loading' ? '…' : <>Verify <ShieldCheck /></>}
        </Button>
      </div>

      <Panel>
        {status === 'idle' && !shown && (
          <EmptyState
            intent="awaiting"
            title="No certificate looked up"
            description="Paste a record hash from a certificate URL or audit result to independently verify its signature."
          />
        )}
        {status === 'error' && (
          <EmptyState intent="error" title="Verification failed" description={error ?? undefined} />
        )}
        {status === 'idle' && shown && (
          <div className="p-4" data-testid="wb-certificate-result">
            <Certificate artifact={shown} verifyResult={verifyResult ?? undefined} readOnly />
          </div>
        )}
      </Panel>
    </div>
  );
}
