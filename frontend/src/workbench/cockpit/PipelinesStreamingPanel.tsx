/* Pipelines & streaming board card — derives its run-row view purely from the
   `pipelines` state Workbench.tsx polls; nothing else here depends on it, so
   the derivation lives locally rather than in the composition root. */
import { cn } from '../../lib/cn';

const runColors: Record<string, string> = { running: 'var(--cyan)', active: 'var(--cyan)', completed: 'var(--accent)', success: 'var(--accent)', failed: 'var(--danger)', error: 'var(--danger)' };

type Props = {
  pipelines: Array<{ name: string; status: string }> | null;
  onDefinePipeline: () => void;
};

export function PipelinesStreamingPanel({ pipelines, onDefinePipeline }: Props) {
  const runs = (pipelines ?? []).map((p) => ({ name: p.name, status: p.status, color: runColors[p.status.toLowerCase()] ?? 'var(--text2)', time: '—', rows: '—' }));

  return (
    <div className="aw-panel" data-testid="wb-pipes">
      <div className="aw-panel-head">
        <div className="aw-panel-title">Pipelines & streaming</div>
        <div className="flex-1" />
        <div className="aw-mono text-[9.5px] font-medium text-[var(--accent)]">PII MASKING ON</div>
      </div>
      <div className="pt-3 px-4 pb-3.5 flex flex-col gap-2.5">
        <div className="aw-mono flex gap-2 text-[10.5px] font-medium flex-wrap">
          <div className="flex items-center gap-1.5 border border-[var(--hair)] rounded-none py-[5px] px-2.5 text-[var(--text2)]"><span className={cn('w-[5px] h-[5px] rounded-full', pipelines?.length ? 'bg-[var(--accent)]' : 'bg-[var(--text3)]')} />{pipelines ? `${pipelines.length} pipeline${pipelines.length === 1 ? '' : 's'} defined` : 'pipelines unavailable'}</div>
        </div>
        {runs.length === 0 && (
          <div className="text-xs text-[var(--text3)] leading-[1.6]">
            No streaming pipelines yet — <button type="button" onClick={onDefinePipeline} className="aw-mono bg-transparent border-none p-0 text-[var(--accent)] cursor-pointer [font:inherit]">define one in the Pipelines view</button> and it appears here.
          </div>
        )}
        {runs.length > 0 && <div className="border border-[var(--hair)] rounded-none overflow-hidden text-[11.5px]">
          <div className="aw-table-head grid grid-cols-[1.6fr_.9fr_.7fr_.8fr]"><div className="py-1.5 px-3">RUN</div><div className="py-1.5 px-3">STATUS</div><div className="py-1.5 px-3">TIME</div><div className="py-1.5 px-3">ROWS</div></div>
          {runs.map((r) => (
            <div key={r.name} className="grid grid-cols-[1.6fr_.9fr_.7fr_.8fr] border-t border-[var(--hair)] items-center">
              <div className="aw-cell">{r.name}</div>
              <div className="py-[7px] px-3 font-semibold" style={{ color: r.color }}>{r.status}</div>
              <div className="aw-cell">{r.time}</div>
              <div className="aw-cell">{r.rows}</div>
            </div>
          ))}
        </div>}
        <div className="text-[10.5px] text-[var(--text3)]">Transforms: filter · aggregate · dedupe · cast · custom SQL → CSV / Parquet / JSON</div>
      </div>
    </div>
  );
}
