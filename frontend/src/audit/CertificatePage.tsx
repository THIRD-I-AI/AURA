import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { auditApi } from './auditApi';
import { Certificate } from './Certificate';
import type { Artifact } from './types';

export function CertificatePage() {
  const { hash } = useParams<{ hash: string }>();
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hash) return;
    auditApi.getArtifact(hash).then(setArtifact).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [hash]);

  if (error) return <div data-testid="certificate-page">Could not load certificate: {error}</div>;
  if (!artifact) return <div data-testid="certificate-page">Loading certificate…</div>;
  return <div data-testid="certificate-page"><Certificate artifact={artifact} /></div>;
}
