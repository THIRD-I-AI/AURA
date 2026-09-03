/* Cockpit stat tiles — Workbench.tsx derives `stats` from live health/ledger/
   history/healing/pipelines state; this just renders the tile grid. */
import { Skeleton } from '@/components/ui-kit/skeleton';

export type Stat = { label: string; value: string; sub: string; subColor: string; loading: boolean };

export function CockpitStats({ stats }: { stats: Stat[] }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3" data-testid="wb-stats">
      {stats.map((st) => (
        <div key={st.label} className="aw-panel rounded-none py-3 px-3.5">
          <div className="text-[11px] text-[var(--text3)] mb-1.5">{st.label}</div>
          {st.loading ? (
            <>
              <Skeleton className="h-[18px] w-12" />
              <Skeleton className="mt-1.5 h-[10.5px] w-20" />
            </>
          ) : (
            <>
              <div className="aw-mono font-semibold text-[18px]">{st.value}</div>
              <div className="text-[10.5px] mt-[3px]" style={{ color: st.subColor }}>{st.sub}</div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
