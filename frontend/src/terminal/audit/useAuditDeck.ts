/**
 * Single live-state hook for the audit command deck. Talks only to the real
 * financialAuditService — it fabricates nothing. Before an audit is run the
 * deck is empty; verification is 'unverified' until a real verify() returns.
 */
import { useCallback, useRef, useState } from 'react';
import {
  financialAuditService,
  type FinancialAuditReport,
  type AuditFinding,
  type HumanOverrideRecord,
} from '../../services/api';
import { SAMPLE_AUDIT_BATCH } from '../../audit/sampleAuditBatch';
import { verificationStateOf, type VerificationState } from './model';

export interface DecisionEntry {
  finding_id: string;
  approved: boolean;
  auditor_id: string;
  at: string;
}

export interface AuditDeckState {
  report: FinancialAuditReport | null;
  findings: AuditFinding[];
  verification: VerificationState;
  decisions: DecisionEntry[];
  busy: boolean;
  error: string | null;
  runAudit: () => Promise<void>;
  verify: () => Promise<void>;
  decide: (finding: AuditFinding, approved: boolean, rationale: string) => Promise<void>;
}

export function useAuditDeck(): AuditDeckState {
  const [report, setReport] = useState<FinancialAuditReport | null>(null);
  const [findings, setFindings] = useState<AuditFinding[]>([]);
  const [verification, setVerification] = useState<VerificationState>('unverified');
  const [decisions, setDecisions] = useState<DecisionEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recordHash = useRef<string | null>(null);

  const runAudit = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await financialAuditService.ensureAuditorToken();
      const r = await financialAuditService.runAudit(SAMPLE_AUDIT_BATCH);
      setReport(r);
      setFindings(r.findings ?? []);
      recordHash.current = r.record_hash;
      setVerification('unverified'); // a fresh report is not yet verified
      setDecisions([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Audit failed');
    } finally {
      setBusy(false);
    }
  }, []);

  const verify = useCallback(async () => {
    if (!recordHash.current) return;
    setError(null);
    try {
      const res = await financialAuditService.verify(recordHash.current);
      setVerification(verificationStateOf(res));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Verification failed');
      setVerification('broken');
    }
  }, []);

  const decide = useCallback(
    async (finding: AuditFinding, approved: boolean, rationale: string) => {
      if (!recordHash.current) return;
      setBusy(true);
      setError(null);
      try {
        const rec: HumanOverrideRecord = await financialAuditService.decide(
          recordHash.current,
          finding.finding_id,
          rationale,
          approved,
        );
        setDecisions((d) => [
          {
            finding_id: rec.finding_id,
            approved: rec.approved,
            auditor_id: rec.human_auditor_id,
            at: new Date().toISOString(),
          },
          ...d,
        ]);
        // A decided finding leaves the pending triage list.
        setFindings((fs) => fs.filter((f) => f.finding_id !== finding.finding_id));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Decision failed');
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return { report, findings, verification, decisions, busy, error, runAudit, verify, decide };
}
