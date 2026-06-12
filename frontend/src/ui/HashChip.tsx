import React from 'react';

import { sanitizeRecordHash } from '../services/api';

export interface HashChipProps {
  hash: string;
  /** Optional verify link — rendered ONLY when the hash passes the
   * Sec-6 hex gate AND the href is a relative same-origin path; the
   * chip is the sanctioned sink for remote hashes, so both inputs are
   * validated independently. */
  verifyHref?: string;
}

/** Relative same-origin only: starts with exactly one '/', so scheme
 * (`javascript:`), protocol-relative (`//evil`), and backslash-bypass
 * (`/\evil`) hrefs all fail closed. */
const isSafeRelativeHref = (href: string): boolean =>
  href.startsWith('/') && !href.startsWith('//') && !href.startsWith('/\\');

export const HashChip: React.FC<HashChipProps> = ({ hash, verifyHref }) => {
  const clean = sanitizeRecordHash(hash);
  if (!clean) {
    return <span className="ui-hashchip ui-hashchip--invalid">invalid hash</span>;
  }
  const safeHref = verifyHref && isSafeRelativeHref(verifyHref) ? verifyHref : undefined;
  const short = `${clean.slice(0, 8)}…${clean.slice(-6)}`;
  return (
    <span className="ui-hashchip">
      <code className="ui-hashchip__hash" title={clean}>{short}</code>
      <button
        type="button"
        className="ui-hashchip__copy"
        aria-label="Copy full hash"
        onClick={() => { void navigator.clipboard?.writeText(clean); }}
      >
        ⧉
      </button>
      {safeHref && (
        <a className="ui-hashchip__verify" href={safeHref}>verify</a>
      )}
    </span>
  );
};
