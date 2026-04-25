import React from 'react';
import { usePresence, type PresencePeer } from '../hooks/usePresence';

export interface PresenceIndicatorProps {
  /** Room id — typically `dashboard:{id}` or `library:{id}`. */
  room: string;
  /** Skip connecting (e.g., while the parent's id is still loading). */
  enabled?: boolean;
}

const AVATAR_SIZE = 22;
const MAX_VISIBLE = 4;

const AVATAR_BASE: React.CSSProperties = {
  width: AVATAR_SIZE,
  height: AVATAR_SIZE,
  borderRadius: '50%',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  fontSize: 10,
  fontWeight: 600,
  color: '#0b1220',
  border: '2px solid var(--bg-surface)',
};

const initialsOf = (label: string): string => label.replace(/^Analyst-/, '').slice(0, 2);

const Avatar: React.FC<{ label: string; color: string; offset?: boolean; ring?: boolean }> = ({
  label, color, offset, ring,
}) => (
  <div
    title={label}
    aria-label={label}
    style={{
      ...AVATAR_BASE,
      background: color,
      marginLeft: offset ? -6 : 0,
      outline: ring ? '1px solid var(--accent)' : undefined,
      outlineOffset: ring ? 1 : undefined,
    }}
  >
    {initialsOf(label)}
  </div>
);

export const PresenceIndicator: React.FC<PresenceIndicatorProps> = ({ room, enabled = true }) => {
  const { me, peers, connected } = usePresence({ room, enabled });

  const total = peers.length + 1;
  const visible: PresencePeer[] = peers.slice(0, MAX_VISIBLE);
  const overflow = peers.length - visible.length;
  const tooltip = [`${me.label} (you)`, ...peers.map((p) => p.label)].join(', ');

  return (
    <div
      role="group"
      aria-label={`Presence: ${total} viewer${total === 1 ? '' : 's'}`}
      title={tooltip}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
    >
      <div style={{ display: 'inline-flex' }}>
        <Avatar label={me.label} color={me.color} ring />
        {visible.map((p) => (
          <Avatar key={p.id} label={p.label} color={p.color} offset />
        ))}
        {overflow > 0 && (
          <div
            aria-label={`+${overflow} more`}
            style={{
              ...AVATAR_BASE,
              background: 'var(--bg-surface-2)',
              color: 'var(--text-secondary)',
              marginLeft: -6,
            }}
          >
            +{overflow}
          </div>
        )}
      </div>
      <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
        {connected ? `${total} viewing` : 'Connecting…'}
      </span>
    </div>
  );
};

export default PresenceIndicator;
