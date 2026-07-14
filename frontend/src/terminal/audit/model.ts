/**
 * Pure, DOM-free model for the audit command deck: risk ordering, triage
 * glyphs, and the honest verification-state derivation. Kept side-effect-free
 * and framework-free so the highest-value invariants are unit-testable in
 * isolation (mirrors terminal/pipeline/topology.ts).
 */
import type { AuditFinding } from '../../services/api';

export type RiskLevel = 'Critical' | 'High' | 'Medium' | 'Low' | 'Unknown';

/** Highest first — the order an auditor should triage. */
export const RISK_RANK: Record<RiskLevel, number> = {
  Critical: 4,
  High: 3,
  Medium: 2,
  Low: 1,
  Unknown: 0,
};

/** Inline glyphs for the triage rail. */
export const RISK_GLYPH: Record<RiskLevel, string> = {
  Critical: '\u25C6', // ◆
  High: '\u25B2',     // ▲
  Medium: '\u25CF',   // ●
  Low: '\u25CB',      // ○
  Unknown: '\u00B7',  // ·
};

/** Normalize a free-form risk_level string to a known level. */
export function riskLevelOf(raw: unknown): RiskLevel {
  const s = String(raw ?? '').toLowerCase();
  if (s === 'critical') return 'Critical';
  if (s === 'high') return 'High';
  if (s === 'medium') return 'Medium';
  if (s === 'low') return 'Low';
  return 'Unknown';
}

/**
 * Stable triage order: highest risk first, then findings that need human
 * review ahead of those that do not, then by finding_id for determinism.
 */
export function sortFindingsByRisk(findings: readonly AuditFinding[]): AuditFinding[] {
  return [...findings].sort((a, b) => {
    const ra = RISK_RANK[riskLevelOf(a.risk_level)];
    const rb = RISK_RANK[riskLevelOf(b.risk_level)];
    if (rb !== ra) return rb - ra;
    const rev = Number(b.requires_human_review) - Number(a.requires_human_review);
    if (rev !== 0) return rev;
    return String(a.finding_id).localeCompare(String(b.finding_id));
  });
}

/**
 * Honest verification state. `null` = never verified this session (awaiting a
 * verify() call) — NOT the same as a failed verification. We never assert
 * "verified" from the signature status alone; only a real verify() result
 * flips this to 'verified' or 'broken'.
 */
export type VerificationState = 'unverified' | 'verified' | 'broken';

export function verificationStateOf(result: { verified: boolean } | null): VerificationState {
  if (result === null) return 'unverified';
  return result.verified ? 'verified' : 'broken';
}

export const VERIFY_GLYPH: Record<VerificationState, string> = {
  unverified: '\u25CC', // ◌  awaiting a verify() call
  verified: '\u2714',   // ✔
  broken: '\u2718',     // ✘
};

/** A finding is actionable (decidable) only when it requires human review. */
export function isDecidable(f: AuditFinding | null): boolean {
  return !!f && f.requires_human_review === true;
}
