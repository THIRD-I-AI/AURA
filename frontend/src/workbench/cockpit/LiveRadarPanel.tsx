/* Live System Radar hero — Workbench.tsx builds `radarModel` from polled
   health/pipelines/healing state; this renders the radar + legend. */
import { cn } from '../../lib/cn';
import { SystemRadar } from '../../components/radar';
import type { SystemRadarModel } from '../../components/radar';

type Props = {
  radarModel: SystemRadarModel;
  gatewayUp: boolean | null;
  onServiceClick: (id: string) => void;
};

export function LiveRadarPanel({ radarModel, gatewayUp, onServiceClick }: Props) {
  return (
    <div className="aw-panel grid grid-cols-[minmax(0,340px)_1fr] gap-0" data-testid="wb-radar">
      <div className="flex items-center justify-center py-[18px] px-2 border-r border-[var(--hair)]">
        <SystemRadar model={radarModel} size={320} onServiceClick={onServiceClick} />
      </div>
      <div className="flex flex-col min-w-0">
        <div className="aw-panel-head" style={{ padding: '14px 18px' }}>
          <span className={cn('w-1.5 h-1.5 rounded-full', gatewayUp && 'animate-[awpulse_2.4s_infinite]')} style={{ background: gatewayUp === false ? 'var(--danger)' : gatewayUp ? 'var(--accent)' : 'var(--text3)' }} />
          <div className="text-[14px] font-semibold">Live System Radar</div>
          <div className="aw-chip aw-pill-outline">real topology</div>
          <div className="flex-1" />
          <div className="text-[11px] text-[var(--text3)]">{radarModel.services.length} services · {radarModel.sources.length} sources</div>
        </div>
        <div className="py-4 px-[18px] flex flex-col gap-3">
          <div className="text-[12.5px] text-[var(--text2)] leading-[1.7]">
            {gatewayUp === false
              ? 'Gateway unreachable — nodes shown from last known topology. Radar resumes when /health responds.'
              : radarModel.services.length === 0
                ? 'Awaiting first health report — service nodes appear as /health responds. Nothing is fabricated.'
                : 'Each node is a backend service from /health; rim points are streaming sources. A ring pulses on drift and an arc traces each recovery in flight.'}
          </div>
          <div className="flex flex-wrap gap-y-2 gap-x-4 text-[11px] text-[var(--text3)]">
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ border: '1.4px solid var(--accent)' }} />service healthy</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ border: '1.4px solid var(--danger)' }} />service down</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ border: '1.4px solid var(--text3)' }} />awaiting</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[var(--warn)]" />drift</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[var(--danger)]" />critical</span>
          </div>
        </div>
      </div>
    </div>
  );
}
