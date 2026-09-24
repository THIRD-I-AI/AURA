/* Per-subject audit history (BUG-159): every audit the tenant's ledger holds for
   one subject (model / cohort / applicant), oldest first, so repeated audits of
   the same thing can be followed and each certificate opened from its row. */
import { useState } from 'react';

import { Panel, PanelHeader, PanelBody } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { financialAuditService, type SubjectHistory as SubjectHistoryData } from '../../services/api';

const INPUT =
  'w-full rounded-none border border-border bg-card p-2 font-mono text-2xs text-card-foreground focus:border-signal focus:outline-none';

function shortHash(h: string | null): string {
  return h ? `${h.slice(0, 12)}…` : '—';
}

export function SubjectHistory() {
  const [subjectId, setSubjectId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SubjectHistoryData | null>(null);

  const lookup = async () => {
    const id = subjectId.trim();
    if (!id) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await financialAuditService.subjectHistory(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel>
      <PanelHeader title="Subject audit history" />
      <PanelBody className="flex flex-col gap-3">
        <p className="text-xs leading-snug text-text-tertiary">
          Look up every audit recorded for one subject — the Subject ID you gave when running an audit —
          with who prepared and reviewed each.
        </p>
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => { e.preventDefault(); void lookup(); }}
        >
          <label className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-text-secondary">
            Subject to look up
            <input
              type="text"
              value={subjectId}
              onChange={(e) => setSubjectId(e.target.value)}
              placeholder="e.g. loan-model-v3"
              className={INPUT}
            />
          </label>
          <Button type="submit" variant="outline" disabled={busy || !subjectId.trim()}>
            {busy ? 'Looking up…' : 'Look up history'}
          </Button>
        </form>

        {error && <p role="alert" className="font-mono text-2xs text-danger">{error}</p>}

        {result && result.count === 0 && (
          <p data-testid="subject-history-empty" className="font-mono text-2xs text-text-tertiary">
            No audits recorded for "{result.subject_id}" in your tenant.
          </p>
        )}

        {result && result.count > 0 && (
          <div className="overflow-x-auto" data-testid="subject-history-table">
            <table className="w-full border-collapse font-mono text-2xs">
              <caption className="pb-1 text-left text-text-tertiary">
                {result.count} audit{result.count === 1 ? '' : 's'} for "{result.subject_id}", oldest first
              </caption>
              <thead>
                <tr className="border-b border-border-hairline text-left uppercase tracking-wider text-text-tertiary">
                  <th className="py-1 pr-3">#</th>
                  <th className="py-1 pr-3">Kind</th>
                  <th className="py-1 pr-3">When</th>
                  <th className="py-1 pr-3">Preparer</th>
                  <th className="py-1 pr-3">Reviewer</th>
                  <th className="py-1">Certificate</th>
                </tr>
              </thead>
              <tbody>
                {result.audits.map((a) => (
                  <tr key={a.seq} className="border-b border-border-hairline align-top text-text-secondary">
                    <td className="py-1 pr-3">{a.seq}</td>
                    <td className="py-1 pr-3">{a.kind}</td>
                    <td className="py-1 pr-3 whitespace-nowrap">{a.ts}</td>
                    <td className="py-1 pr-3">{a.preparer_id}</td>
                    <td className="py-1 pr-3">{a.reviewer_id ?? '—'}</td>
                    <td className="py-1">
                      {a.cert_hash ? (
                        <a
                          href={`/certificate/${encodeURIComponent(a.cert_hash)}`}
                          title={a.cert_hash}
                          className="!text-signal underline underline-offset-2"
                        >
                          {shortHash(a.cert_hash)}
                        </a>
                      ) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </PanelBody>
    </Panel>
  );
}
