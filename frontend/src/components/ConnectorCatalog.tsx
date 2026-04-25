import React, { useEffect, useState } from 'react';
import { connectorService, type ConnectorSpec } from '../services/api';

const kindLabel: Record<string, string> = {
  relational: 'Relational',
  warehouse: 'Warehouse',
  embedded: 'Embedded',
  stream: 'Stream',
};

const cardStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 6,
  padding: 'var(--space-3) var(--space-4)',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--radius-md)',
  minWidth: 0,
};

const ConnectorCatalog: React.FC = () => {
  const [specs, setSpecs] = useState<ConnectorSpec[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    connectorService
      .registry()
      .then(list => { if (!cancelled) setSpecs(list); })
      .catch(err => { if (!cancelled) setError(err?.message ?? 'Failed to load connectors'); });
    return () => { cancelled = true; };
  }, []);

  if (error) {
    return (
      <p style={{ margin: 0, fontSize: 'var(--font-sm)', color: '#f87171' }}>
        Could not load connector registry: {error}
      </p>
    );
  }

  if (!specs) {
    return (
      <p style={{ margin: 0, fontSize: 'var(--font-sm)', color: 'var(--text-disabled)' }}>
        Loading connectors…
      </p>
    );
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 'var(--space-3)' }}>
      {specs.map(spec => (
        <div key={spec.id} style={cardStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 18, lineHeight: 1 }}>{spec.icon || '🔌'}</span>
            <span style={{ fontSize: 'var(--font-sm)', fontWeight: 600, color: 'var(--text-primary)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {spec.name}
            </span>
            <span
              style={{
                fontSize: '10px', fontWeight: 700, padding: '2px 8px',
                borderRadius: 'var(--radius-full)',
                background: spec.available ? 'rgba(52, 211, 153, 0.12)' : 'var(--bg-sunken)',
                border: `1px solid ${spec.available ? 'rgba(52, 211, 153, 0.35)' : 'var(--border-subtle)'}`,
                color: spec.available ? '#34d399' : 'var(--text-disabled)',
              }}
              title={spec.unavailable_reason || undefined}
            >
              {spec.available ? 'Ready' : 'Unavailable'}
            </span>
          </div>
          <p style={{ margin: 0, fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
            {spec.description}
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 2 }}>
            <span style={{ fontSize: '10px', fontWeight: 600, padding: '1px 6px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-sunken)', color: 'var(--text-tertiary)' }}>
              {kindLabel[spec.kind] ?? spec.kind}
            </span>
            {spec.capabilities.map(cap => (
              <span key={cap} style={{ fontSize: '10px', fontWeight: 500, padding: '1px 6px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-sunken)', color: 'var(--text-disabled)', fontFamily: 'var(--font-mono)' }}>
                {cap}
              </span>
            ))}
          </div>
          {!spec.available && spec.unavailable_reason && (
            <p style={{ margin: '4px 0 0', fontSize: '10px', color: '#fbbf24' }}>
              {spec.unavailable_reason}
            </p>
          )}
        </div>
      ))}
    </div>
  );
};

export default ConnectorCatalog;
