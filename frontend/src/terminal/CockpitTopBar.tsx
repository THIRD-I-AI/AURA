import { LAYOUT_NAMES } from './layoutStore';

interface Props {
  onApplyLayout: (name: (typeof LAYOUT_NAMES)[number]) => void;
  onOpenPalette: () => void;
  onBack: () => void;
}

export function CockpitTopBar({ onApplyLayout, onOpenPalette, onBack }: Props) {
  return (
    <header className="cockpit-topbar" data-testid="cockpit-topbar">
      <span className="cockpit-brand">AURA · Terminal</span>
      <nav className="cockpit-layouts">
        {LAYOUT_NAMES.map((name) => (
          <button key={name} data-testid={`layout-${name}`} onClick={() => onApplyLayout(name)}>
            {name[0].toUpperCase() + name.slice(1)}
          </button>
        ))}
      </nav>
      <div className="cockpit-actions">
        <button data-testid="open-palette" onClick={onOpenPalette}>⌘K</button>
        <button data-testid="back-to-app" onClick={onBack}>← Back to app</button>
      </div>
    </header>
  );
}
