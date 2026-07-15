import { describe, expect, it } from 'vitest';
import type { AuditFinding } from '../../../services/api';
import {
  RISK_RANK,
  RISK_GLYPH,
  VERIFY_GLYPH,
  riskLevelOf,
  sortFindingsByRisk,
  verificationStateOf,
  isDecidable,
  type RiskLevel,
} from '../model';

function finding(over: Partial<AuditFinding>): AuditFinding {
  return {
    finding_id: 'f',
    pcaob_standard: 'AS 2401',
    risk_level: 'Low',
    description: 'x',
    evidence_payload: {},
    requires_human_review: false,
    ...over,
  };
}

const LEVELS: RiskLevel[] = ['Critical', 'High', 'Medium', 'Low', 'Unknown'];

describe('audit model — risk taxonomy', () => {
  it('ranks strictly Critical > High > Medium > Low > Unknown', () => {
    expect(RISK_RANK.Critical).toBeGreaterThan(RISK_RANK.High);
    expect(RISK_RANK.High).toBeGreaterThan(RISK_RANK.Medium);
    expect(RISK_RANK.Medium).toBeGreaterThan(RISK_RANK.Low);
    expect(RISK_RANK.Low).toBeGreaterThan(RISK_RANK.Unknown);
  });

  it('has a distinct glyph for every risk level', () => {
    const glyphs = LEVELS.map((l) => RISK_GLYPH[l]);
    for (const g of glyphs) expect(g).toBeTruthy();
    expect(new Set(glyphs).size).toBe(LEVELS.length);
  });

  it('normalizes free-form risk strings case-insensitively, unknown fallback', () => {
    expect(riskLevelOf('critical')).toBe('Critical');
    expect(riskLevelOf('HIGH')).toBe('High');
    expect(riskLevelOf('Medium')).toBe('Medium');
    expect(riskLevelOf('low')).toBe('Low');
    expect(riskLevelOf('bogus')).toBe('Unknown');
    expect(riskLevelOf(undefined)).toBe('Unknown');
    expect(riskLevelOf(null)).toBe('Unknown');
  });
});

describe('audit model — triage sort', () => {
  it('orders highest risk first', () => {
    const out = sortFindingsByRisk([
      finding({ finding_id: 'lo', risk_level: 'Low' }),
      finding({ finding_id: 'cr', risk_level: 'Critical' }),
      finding({ finding_id: 'me', risk_level: 'Medium' }),
    ]);
    expect(out.map((f) => f.finding_id)).toEqual(['cr', 'me', 'lo']);
  });

  it('within a risk level, findings needing human review come first', () => {
    const out = sortFindingsByRisk([
      finding({ finding_id: 'a', risk_level: 'High', requires_human_review: false }),
      finding({ finding_id: 'b', risk_level: 'High', requires_human_review: true }),
    ]);
    expect(out.map((f) => f.finding_id)).toEqual(['b', 'a']);
  });

  it('is a pure function — does not mutate its input', () => {
    const input = [
      finding({ finding_id: 'lo', risk_level: 'Low' }),
      finding({ finding_id: 'cr', risk_level: 'Critical' }),
    ];
    const before = input.map((f) => f.finding_id);
    sortFindingsByRisk(input);
    expect(input.map((f) => f.finding_id)).toEqual(before);
  });
});

describe('audit model — verification honesty', () => {
  it('null (never verified this session) maps to unverified, NOT verified', () => {
    expect(verificationStateOf(null)).toBe('unverified');
  });

  it('only a real verify() result flips the state', () => {
    expect(verificationStateOf({ verified: true })).toBe('verified');
    expect(verificationStateOf({ verified: false })).toBe('broken');
  });

  it('has a distinct glyph for each verification state', () => {
    const glyphs = [VERIFY_GLYPH.unverified, VERIFY_GLYPH.verified, VERIFY_GLYPH.broken];
    for (const g of glyphs) expect(g).toBeTruthy();
    expect(new Set(glyphs).size).toBe(3);
  });
});

describe('audit model — decidability', () => {
  it('is decidable only when human review is required', () => {
    expect(isDecidable(finding({ requires_human_review: true }))).toBe(true);
    expect(isDecidable(finding({ requires_human_review: false }))).toBe(false);
    expect(isDecidable(null)).toBe(false);
  });
});
