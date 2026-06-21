import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { RFAuraNode } from './layout';

const KIND_GLYPH: Record<string, string> = {
  table: '▤',
  saved_query: '⌘',
  dashboard: '▦',
};

/** Custom React Flow node — a glowing, on-brand disc sized by connection degree. */
export function AuraNode({ data, selected }: NodeProps<RFAuraNode>) {
  const diameter = 30 + Math.min(data.degree, 6) * 7;
  return (
    <div
      className={`aura-node aura-node--${data.kind}${selected ? ' is-selected' : ''}`}
      title={`${data.label} · ${data.kind}`}
    >
      <Handle type="target" position={Position.Top} className="aura-node__handle" isConnectable={false} />
      <div className="aura-node__disc" style={{ width: diameter, height: diameter }}>
        <span className="aura-node__glyph" aria-hidden="true">{KIND_GLYPH[data.kind] ?? '●'}</span>
      </div>
      <span className="aura-node__label">{data.label}</span>
      <Handle type="source" position={Position.Bottom} className="aura-node__handle" isConnectable={false} />
    </div>
  );
}
