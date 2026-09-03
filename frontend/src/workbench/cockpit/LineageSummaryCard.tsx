/* Lineage & provenance summary card — links out to the full Constellation
   graph; only needs the ledger state for its replay footnote. */

type Props = {
  ledger: { no: string; hash: string; intact: boolean } | null;
};

export function LineageSummaryCard({ ledger }: Props) {
  return (
    <div className="aw-panel" data-testid="wb-lineage">
      <div className="aw-panel-head"><div className="aw-panel-title">Lineage &amp; provenance</div></div>
      <div className="py-3.5 px-4 flex flex-col gap-2.5">
        <div className="text-xs text-[var(--text2)] leading-[1.65]">
          Dataset-to-finding lineage renders live in the <strong>Constellation</strong> graph —
          uploaded datasets, derived metrics, and signed findings as a navigable graph.
        </div>
        <a href="/app" className="aw-mono self-start text-[10.5px] font-semibold border border-[var(--accent-bd)] rounded-none py-1.5 px-3 no-underline" style={{ color: 'var(--accent)' }}>Open Constellation →</a>
        {ledger && <div className="text-[10.5px] text-[var(--text3)] leading-[1.6]">Every signed artifact is replayable from ledger {ledger.no}.</div>}
      </div>
    </div>
  );
}
