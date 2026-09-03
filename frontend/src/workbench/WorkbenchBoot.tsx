/* Boot sequence screen — shown while Workbench.tsx steps `bootIdx` through
   BOOT_STAGES before flipping to the app view. */
import { cn } from '../lib/cn';
import { BOOT_STAGES } from './bootStages';

export function WorkbenchBoot({ bootIdx }: { bootIdx: number }) {
  return (
    <div className="aw" data-testid="wb-boot">
      <div className="flex flex-1 min-h-screen flex-col items-center justify-center gap-[30px]">
        <div className="flex items-center gap-2.5"><span className="w-2.5 h-2.5 bg-[var(--accent)] rounded-none animate-[awpulse_1.4s_infinite]" /><span className="aw-display font-bold text-[18px] tracking-widest">AURA</span></div>
        <div className="flex w-[340px] flex-col gap-2.5">
          {BOOT_STAGES.map((label, i) => (
            <div key={label} className={cn('aw-mono flex items-center gap-2.5 text-[11px] font-medium', i < bootIdx ? 'text-[var(--accent)]' : i === bootIdx ? 'text-[var(--text)]' : 'text-[var(--text3)]')}>
              <span className="w-3.5 text-center">{i < bootIdx ? '✓' : i === bootIdx ? '◌' : '·'}</span>{label}
            </div>
          ))}
        </div>
        <div className="w-[340px] h-[3px] bg-[var(--raised)] rounded-none overflow-hidden"><div className="h-full bg-[var(--accent)] rounded-none" style={{ transition: 'width .45s ease', width: Math.min(100, Math.round((bootIdx / BOOT_STAGES.length) * 100)) + '%' }} /></div>
      </div>
    </div>
  );
}
