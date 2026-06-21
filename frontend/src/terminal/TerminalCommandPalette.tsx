import { useEffect, useMemo, useRef, useState } from 'react';
import { fuzzyScore } from '../utils/fuzzyScore';
import type { TerminalCommand } from './commands';

interface Props {
  open: boolean;
  onClose: () => void;
  commands: TerminalCommand[];
}

export function TerminalCommandPalette({ open, onClose, commands }: Props) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
      inputRef.current?.focus();
    }
  }, [open]);

  const ranked = useMemo(
    () =>
      commands
        .map((c) => ({ c, score: fuzzyScore(c.label, query) }))
        .filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score)
        .map((x) => x.c),
    [commands, query],
  );

  if (!open) return null;

  const run = (cmd?: TerminalCommand) => {
    cmd?.run();
    onClose();
  };

  return (
    <div className="palette-overlay" data-testid="terminal-palette" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          data-testid="palette-input"
          value={query}
          placeholder="Type a command…"
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setActive((i) => Math.min(i + 1, ranked.length - 1));
            } else if (e.key === 'ArrowUp') {
              e.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (e.key === 'Enter') {
              run(ranked[active]);
            } else if (e.key === 'Escape') {
              onClose();
            }
          }}
        />
        <ul className="palette-list">
          {ranked.map((c, i) => (
            <li key={c.id} className={i === active ? 'is-active' : ''} onClick={() => run(c)}>
              <span className="palette-group">{c.group}</span> {c.label}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
