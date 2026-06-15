import { Link } from 'react-router-dom';
import { auditApi } from './auditApi';
import type { Artifact, VerifyResult } from './types';

/** Plain-English verdict from the estimates (S31b ships no narrative field). */
function verdict(a: Artifact): string {
  // Skip failed estimators; coerce numbers (live path = number, replay = string).
  const usable = a.estimates.filter(
    (e) => !e.error && e.point !== undefined && e.point !== null && Number.isFinite(Number(e.point)),
  );
  if (usable.length === 0) return 'Audit complete — see estimator detail.';

  const avg = usable.reduce((s, e) => s + Number(e.point), 0) / usable.length;
  if (Math.abs(avg) < 0.02) {
    return 'No material disparate impact detected after causal adjustment.';
  }

  // A material point estimate is only "detected" if it's also statistically
  // significant — every estimator's 95% CI must exclude zero. Claiming impact
  // on a CI that straddles zero would overclaim (this is a compliance artifact).
  const ciExcludesZero = (e: (typeof usable)[number]) => {
    const lo = Number(e.ci_lower);
    const hi = Number(e.ci_upper);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return false;
    return (lo > 0 && hi > 0) || (lo < 0 && hi < 0);
  };
  const withCi = usable.filter((e) => Number.isFinite(Number(e.ci_lower)) && Number.isFinite(Number(e.ci_upper)));
  if (withCi.length > 0 && !withCi.every(ciExcludesZero)) {
    return 'A point estimate suggests an effect, but it is not statistically significant after adjustment (95% intervals include zero).';
  }
  return 'Disparate impact detected after causal adjustment.';
}

export function Certificate({ artifact, verifyResult, readOnly = false }: {
  artifact: Artifact;
  verifyResult?: VerifyResult;
  readOnly?: boolean;
}) {
  // Public read-only surfaces must NOT trust the artifact's self-reported
  // status — only the server-recomputed verifyResult. Without one, stay neutral.
  const badge: { tone: 'ok' | 'bad' | 'neutral'; label: string } = verifyResult
    ? verifyResult.verified
      ? { tone: 'ok', label: '✓ ED25519 signed' }
      // A "signed" artifact that fails server verification is tampered or
      // signed by the wrong key — say "invalid", not the misleading
      // "✕ signature signed" that echoing signature_status produced.
      : { tone: 'bad', label: verifyResult.signature_status === 'signed' ? '✕ signature invalid' : '✕ ' + verifyResult.signature_status }
    : readOnly
      ? { tone: 'neutral', label: 'signature not independently verified' }
      : artifact.signature_status === 'signed'
        ? { tone: 'ok', label: '✓ ED25519 signed' }
        : { tone: 'bad', label: '✕ ' + artifact.signature_status };
  const badgeColor = badge.tone === 'ok' ? 'var(--green)' : badge.tone === 'bad' ? 'var(--red)' : 'var(--text-tertiary)';
  const badgeBg = badge.tone === 'ok' ? 'var(--green-bg, rgba(76,175,80,0.15))' : badge.tone === 'bad' ? 'var(--red-bg, rgba(244,67,54,0.15))' : 'var(--bg-base)';

  return (
    <div data-testid="certificate" style={{ maxWidth: 720, margin: '0 auto', background: 'var(--bg-surface)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-8)', textAlign: 'center' }}>
      <div data-testid="cert-signature-badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)', padding: 'var(--space-1) var(--space-3)', borderRadius: 'var(--radius-full)', background: badgeBg, color: badgeColor, fontWeight: 600, fontSize: 'var(--font-sm)' }}>
        {badge.label}
      </div>

      {artifact.degraded && (
        <p data-testid="cert-degraded" style={{ marginTop: 'var(--space-3)', fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>
          Served a verified cached result.
        </p>
      )}

      <h1 style={{ fontSize: 'var(--font-2xl)', margin: 'var(--space-5) 0 var(--space-2)' }}>Audit Certificate</h1>
      {/* Prefer the backend's signed verdict (S33) so the web cert matches the
          PDF exactly; fall back to the local rule when `rendered` is absent
          (e.g. the public verify page reconstructs an artifact without it). */}
      <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--space-6)' }}>{artifact.rendered?.verdict?.label ?? verdict(artifact)}</p>

      <div style={{ textAlign: 'left', background: 'var(--bg-base)', borderRadius: 'var(--radius-md)', padding: 'var(--space-4)', marginBottom: 'var(--space-6)' }}>
        <div style={{ fontSize: 'var(--font-xs)', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-tertiary)' }}>Record hash</div>
        <div data-testid="cert-hash" style={{ fontFamily: 'monospace', wordBreak: 'break-all', color: 'var(--accent)' }}>{artifact.audit_record_hash}</div>
        <div data-testid="cert-key-source" style={{ marginTop: 'var(--space-3)', fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>Key source: {artifact.signing_key_source}</div>
      </div>

      {verifyResult && (
        <div data-testid="cert-verify-status" style={{ marginBottom: 'var(--space-5)', fontWeight: 600, color: verifyResult.verified ? 'var(--green)' : 'var(--red)' }}>
          {verifyResult.verified ? 'Independently verified ✓' : `NOT verified — ${verifyResult.reason ?? verifyResult.signature_status}`}
        </div>
      )}

      {!readOnly && (
        <div style={{ display: 'flex', gap: 'var(--space-3)', justifyContent: 'center' }}>
          <a data-testid="cert-download-pdf" href={auditApi.pdfUrl(artifact.audit_record_hash)} target="_blank" rel="noreferrer" style={{ padding: 'var(--space-2) var(--space-5)', background: 'var(--accent)', color: '#fff', borderRadius: 'var(--radius-md)', textDecoration: 'none' }}>Download PDF</a>
          <Link data-testid="cert-verify-link" to={`/verify/${artifact.audit_record_hash}`} style={{ padding: 'var(--space-2) var(--space-5)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-md)', textDecoration: 'none', color: 'var(--text-primary)' }}>Verify independently</Link>
        </div>
      )}
    </div>
  );
}
