import React from 'react';

import { sanitizeRecordHash } from '../services/api';

export interface HashChipProps {
  hash: string;
  /** Optional verify link — rendered ONLY when the hash passes the
   * Sec-6 hex gate; the chip is a sanctioned sink for remote hashes. */
  verifyHref?: string;
}

export const HashChip: React.FC<HashChipProps> = ({ hash, verifyHref }) => {
  const clean = sanitizeRecordHash(hash);
  if (!clean) {
    return <span className="ui-hashchip ui-hashchip--invalid">invalid hash</span>;
  }
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
      {verifyHref && (
        <a className="ui-hashchip__verify" href={verifyHref}>verify</a>
      )}
    </span>
  );
};
