/* Forensic audit demo panel. `cf` + `runCf` stay owned by Workbench.tsx (the
   command palette's "Run counterfactual audit" entry calls the same runCf,
   with a ref guard against a stale closure — see Workbench.tsx). Only the
   audience toggle is local, presentation-only state. */
import { useState } from 'react';
import { cn } from '../../lib/cn';
import type { CfState } from './types';
import { CF_STAGES } from './cfStages';

type Props = {
  cf: CfState;
  runCf: () => void;
  selectNav: (name: string) => void;
};

export function ForensicAuditPanel({ cf, runCf, selectNav }: Props) {
  const [audience, setAudience] = useState<'operator' | 'auditor' | 'analyst'>('operator');

  return (
    <div className="aw-panel" data-testid="wb-cf">
      <div className="aw-panel-head" style={{ padding: '14px 18px' }}>
        <div className="text-[14px] font-semibold">Forensic audit</div>
        <div className="aw-mono text-[10px] font-medium text-[var(--text3)]">{cf.status === 'done' && cf.hash ? cf.hash.slice(0, 12) + '…' : 'AS-2401 · AS-2201 · AS-2305'}</div>
        <div className="flex-1" />
        <div className="aw-mono flex text-[10px] font-semibold border border-[var(--border)] rounded-none overflow-hidden">
          {(['operator', 'auditor', 'analyst'] as const).map((a) => (
            <div key={a} onClick={() => setAudience(a)} className="cursor-pointer py-1 px-[9px]" style={{ color: a === audience ? 'var(--accent)' : 'var(--text3)', background: a === audience ? 'var(--accent-dim)' : 'transparent' }}>{a.toUpperCase()}</div>
          ))}
        </div>
      </div>
      <div className="py-4 px-[18px] flex flex-col gap-[13px]">
        <div className="text-[13px] leading-[1.5] text-[var(--text2)] italic">
          "Run the full forensic sweep — Benford, cutoff, three-way match, segregation of duties,
          expectation analytics — and sign the findings to the ledger."
        </div>
        {/* Say plainly which half is a demo. The INPUT is a fixed sample
            ledger (GET …/audit/financial/demo); the signing, hashing and
            ledger chaining that follow are the real production pipeline.
            Without this the tile reads as if it had audited the user's books. */}
        <div className="aw-mono text-[10.5px] leading-[1.6] text-[var(--text3)] border border-[var(--hair)] rounded-none py-[7px] px-[9px] bg-[var(--sunken)]" data-testid="wb-cf-demo-notice">
          DEMO SCENARIO — runs a fixed sample ledger. The signature, record hash and
          ledger chaining below are real. To audit your own ledger, use{' '}
          <button
            type="button"
            onClick={() => selectNav('Exception Queue')}
            style={{ background: 'none', border: 'none', padding: 0, font: 'inherit', color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline' }}
          >Exception Queue</button>.
        </div>
        {cf.status === 'running' && (
          <div className="flex flex-col gap-[7px] py-1.5 px-0">
            {CF_STAGES.map((label, i) => (
              <div key={label} className="aw-mono flex items-center gap-[9px] text-[11px] font-medium" style={{ color: i < cf.stageIdx ? 'var(--accent)' : i === cf.stageIdx ? 'var(--text)' : 'var(--text3)' }}>
                <span className="w-3.5 text-center">{i < cf.stageIdx ? '✓' : i === cf.stageIdx ? '◌' : '·'}</span>{label}
              </div>
            ))}
          </div>
        )}
        {cf.status === 'error' && (
          <div className="text-[12px] text-[var(--danger)] leading-[1.6]">
            Audit service unreachable — {cf.message}. Start the counterfactual service and retry.
          </div>
        )}
        {cf.status === 'done' && (
          <>
            <div className="flex items-baseline gap-3">
              <div className="aw-mono font-semibold text-[30px]" style={{ color: cf.nFindings ? 'var(--danger)' : 'var(--accent)' }}>{cf.nFindings ?? '—'}</div>
              <div className="text-[12px] text-[var(--text3)]">findings, signed &amp; ledger-chained</div>
            </div>
            {cf.materiality && <div className="aw-mono text-[11px] font-medium text-[var(--text2)]">AS-2110 materiality threshold {cf.materiality}</div>}
            {(audience === 'auditor' || audience === 'analyst') && (
              <div className="aw-mono bg-[var(--sunken)] border border-[var(--hair)] rounded-none py-[11px] px-[13px] text-[10.5px] leading-[1.7] text-[var(--text2)] whitespace-pre-wrap max-h-[180px] overflow-y-auto">{cf.raw}</div>
            )}
          </>
        )}
        <div className="flex items-center gap-2 pt-1 border-t border-[var(--hair)]">
          {cf.status === 'done' && cf.hash && (
            <a href={cf.verifyUrl ?? '#'} className="aw-mono text-[10px] font-medium bg-[var(--sunken)] border border-[var(--hair)] rounded-none py-[3px] px-2 no-underline" style={{ color: 'var(--accent)' }}>record {cf.hash.slice(0, 10)}… · verify ↗</a>
          )}
          <div className="flex-1" />
          <button onClick={runCf} disabled={cf.status === 'running'} className={cn('aw-btn-accent text-[11.5px] py-[5px] px-[11px] rounded-none', cf.status === 'running' && 'opacity-60')}>
            {cf.status === 'idle' ? 'Run demo audit' : cf.status === 'running' ? 'Running…' : 'Re-run demo audit'}
          </button>
        </div>
      </div>
    </div>
  );
}
