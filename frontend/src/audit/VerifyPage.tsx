import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { auditApi } from './auditApi';
import { Certificate } from './Certificate';
import type { Artifact, VerifyResult } from './types';

export function VerifyPage() {
  const { hash } = useParams<{ hash: string }>();
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hash) return;
    // Verdict comes from the SERVER-recomputed verify endpoint, never the
    // artifact's self-reported status (compliance requirement).
    auditApi.verify(hash).then(setVerifyResult).catch((e) => setError(e instanceof Error ? e.message : String(e)));
    auditApi.getArtifact(hash).then(setArtifact).catch(() => { /* artifact body is presentational only */ });
  }, [hash]);

  if (error) return <div data-testid="verify-page">Verification unavailable: {error}</div>;
  if (!verifyResult) return <div data-testid="verify-page">Verifying…</div>;

  const shown: Artifact = artifact ?? {
    audit_record_hash: verifyResult.record_hash,
    estimates: [], refutations: [],
    signature_status: verifyResult.signature_status,
    signing_key_source: verifyResult.signing_key_source,
  };

  return (
    <div data-testid="verify-page">
      <Certificate artifact={shown} verifyResult={verifyResult} readOnly />
    </div>
  );
}
