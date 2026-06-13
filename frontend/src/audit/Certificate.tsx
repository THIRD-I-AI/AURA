import { Link } from 'react-router-dom';
import { auditApi } from './auditApi';
import { CertificateTheme } from '../ui/CertificateTheme';
import { Badge } from '../ui/Badge';
import type { BadgeStatus } from '../ui/status';
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
      : { tone: 'bad', label: '✕ signature ' + verifyResult.signature_status }
    : readOnly
      ? { tone: 'neutral', label: 'signature not independently verified' }
      : artifact.signature_status === 'signed'
        ? { tone: 'ok', label: '✓ ED25519 signed' }
        : { tone: 'bad', label: '✕ signature ' + artifact.signature_status };
  // Map the compliance-decided tone to a presentational Badge status — strip
  // the leading glyph from the label since the Badge supplies its own.
  const badgeStatus: BadgeStatus = badge.tone === 'ok' ? 'verified' : badge.tone === 'bad' ? 'danger' : 'neutral';
  const badgeText = badge.label.replace(/^[✓✕]\s*/, '');

  return (
    <CertificateTheme>
      <div data-testid="certificate" className="aud-cert">
        <div className="aud-cert__seal">
          <span className="aud-cert__doctype">Certificate of Audit</span>
          <span data-testid="cert-signature-badge"><Badge status={badgeStatus}>{badgeText}</Badge></span>
        </div>

        {artifact.degraded && (
          <p data-testid="cert-degraded" className="aud-cert__verdict">Served a verified cached result.</p>
        )}

        <h1 className="aud-cert__title">Audit Certificate</h1>
        {/* Prefer the backend's signed verdict (S33) so the web cert matches the
            PDF exactly; fall back to the local rule when `rendered` is absent
            (e.g. the public verify page reconstructs an artifact without it). */}
        <p className="aud-cert__verdict">{artifact.rendered?.verdict?.label ?? verdict(artifact)}</p>

        <div className="aud-cert__evidence">
          <div className="aud-cert__label">Record hash</div>
          <div data-testid="cert-hash" className="aud-cert__hash">{artifact.audit_record_hash}</div>
          <div data-testid="cert-key-source" className="aud-cert__keysrc">Key source: {artifact.signing_key_source}</div>
        </div>

        {verifyResult && (
          <div
            data-testid="cert-verify-status"
            className={`aud-cert__verifystatus aud-cert__verifystatus--${verifyResult.verified ? 'ok' : 'bad'}`}
          >
            {verifyResult.verified ? 'Independently verified ✓' : `NOT verified — ${verifyResult.reason ?? verifyResult.signature_status}`}
          </div>
        )}

        {!readOnly && (
          <div className="aud-cert__actions">
            <a data-testid="cert-download-pdf" href={auditApi.pdfUrl(artifact.audit_record_hash)} target="_blank" rel="noreferrer" className="aud-cert__pdf">Download PDF</a>
            <Link data-testid="cert-verify-link" to={`/verify/${artifact.audit_record_hash}`} className="ui-btn ui-btn--secondary ui-btn--md aud-cert__verify-link">Verify independently</Link>
          </div>
        )}
      </div>
    </CertificateTheme>
  );
}
