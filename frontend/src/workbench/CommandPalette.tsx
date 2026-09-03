/* ⌘K command palette — nav jumps, "Run counterfactual audit", "Sign out".
   `commands` is the already-filtered/computed list built in Workbench.tsx
   (needs setNav/selectNav/runCf/logout, which stay owned there). */
import type { RefObject } from 'react';

export type Command = { title: string; hint: string; run: () => void };

type Props = {
  paletteOpen: boolean;
  onClose: () => void;
  paletteQ: string;
  setPaletteQ: (q: string) => void;
  paletteInput: RefObject<HTMLInputElement | null>;
  commands: Command[];
};

export function CommandPalette({ paletteOpen, onClose, paletteQ, setPaletteQ, paletteInput, commands }: Props) {
  if (!paletteOpen) return null;
  return (
    <div onClick={onClose} className="fixed inset-0 bg-[var(--overlay)] z-[100] flex justify-center pt-[120px]" data-testid="wb-palette">
      <div onClick={(e) => e.stopPropagation()} className="w-[520px] h-fit bg-[var(--surface)] border border-[var(--border)] rounded-none shadow-[0_24px_60px_rgba(0,0,0,.35)] overflow-hidden animate-[awup_.18s_ease]">
        <input ref={paletteInput} value={paletteQ} onChange={(e) => setPaletteQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && commands[0]) commands[0].run(); }} placeholder="Type a command or destination…" className="w-full box-border bg-transparent border-0 border-b border-[var(--hair)] py-[14px] px-[18px] font-ui font-normal text-[14px] text-[var(--text)] outline-none" />
        <div className="max-h-[320px] overflow-y-auto p-1.5">
          {commands.map((c) => (
            <div key={c.title} onClick={c.run} className="aw-hover-raise cursor-pointer flex justify-between items-center py-[9px] px-3 rounded-none text-[13px]">
              <span>{c.title}</span><span className="aw-mono text-[9.5px] font-medium text-[var(--text3)]">{c.hint}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
