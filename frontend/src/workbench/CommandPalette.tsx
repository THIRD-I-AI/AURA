/* ⌘K command palette — nav jumps, "Run counterfactual audit", "Sign out".
   `commands` is the already-filtered/computed list built in Workbench.tsx
   (needs setNav/selectNav/runCf/logout, which stay owned there). */
import { useState, type KeyboardEvent, type RefObject } from 'react';

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
  // BUG-256: items were mouse-only and Enter could only ever run the first match.
  const [active, setActive] = useState(0);
  if (!paletteOpen) return null;
  const current = Math.min(active, Math.max(commands.length - 1, 0));
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(Math.min(current + 1, commands.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(current - 1, 0)); }
    else if (e.key === 'Enter' && commands[current]) commands[current].run();
  };
  return (
    <div onClick={onClose} className="fixed inset-0 bg-[var(--overlay)] z-[100] flex justify-center pt-[120px]" data-testid="wb-palette">
      <div onClick={(e) => e.stopPropagation()} className="w-[520px] h-fit bg-[var(--surface)] border border-[var(--border)] rounded-none shadow-[0_24px_60px_rgba(0,0,0,.35)] overflow-hidden animate-[awup_.18s_ease]">
        <input ref={paletteInput} value={paletteQ} onChange={(e) => { setActive(0); setPaletteQ(e.target.value); }} onKeyDown={onKeyDown} role="combobox" aria-expanded aria-controls="wb-palette-list" aria-activedescendant={commands[current] ? `wb-palette-opt-${current}` : undefined} aria-label="Command palette" placeholder="Type a command or destination…" className="w-full box-border bg-transparent border-0 border-b border-[var(--hair)] py-[14px] px-[18px] font-ui font-normal text-[14px] text-[var(--text)] outline-none" />
        <div id="wb-palette-list" role="listbox" className="max-h-[320px] overflow-y-auto p-1.5">
          {commands.map((c, i) => (
            <div key={c.title} id={`wb-palette-opt-${i}`} role="option" aria-selected={i === current} onClick={c.run} onMouseEnter={() => setActive(i)} className={`aw-hover-raise cursor-pointer flex justify-between items-center py-[9px] px-3 rounded-none text-[13px]${i === current ? ' bg-[var(--sunken)]' : ''}`}>
              <span>{c.title}</span><span className="aw-mono text-[9.5px] font-medium text-[var(--text3)]">{c.hint}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
