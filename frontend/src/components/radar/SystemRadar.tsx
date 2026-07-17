/* Live System Radar — the shared cockpit centerpiece.
   Mounted in BOTH the terminal deck and the workbench hero. Pure
   presentational: it takes a SystemRadarModel and renders; it never fetches,
   so it cannot break from data coupling and stays trivially reusable.

   Honesty rules it enforces visually:
     • gatewayUp === null  → hub shows a dim "awaiting" ring (◌), not green
     • a service with up === null → dim node, never a healthy green dot
     • sources pulse ONLY at their real severity; 'none' sits idle
     • recovery arcs animate ONLY while a shim is actually deploying
   Motion drives every transition; AnimatePresence animates nodes/sources in
   and out as the live topology changes (new pipeline, recovered source).
   Respects prefers-reduced-motion via the CSS media query in workbench.css
   and Motion's own reduced-motion handling. */
import { useMemo } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'motion/react';
import type { SystemRadarModel, Severity } from './types';
import { SEVERITY_COLOR, SEVERITY_RANK } from './types';
import { useRadarLayout } from './useRadarLayout';

export interface SystemRadarProps {
  model: SystemRadarModel;
  /** Square viewport size in px. Default 320. */
  size?: number;
  /** Optional click handler for a service node. */
  onServiceClick?: (id: string) => void;
  className?: string;
}

const VIEW = 200; // internal coordinate space (centered at 0,0 → -100..100)

export function SystemRadar({ model, size = 320, onServiceClick, className }: SystemRadarProps) {
  const reduce = useReducedMotion();
  const serviceR = 62;
  const nodes = useRadarLayout(model.services, serviceR);

  // Sources orbit the rim at evenly-spaced angles (stable by index).
  const rimR = 92;
  const sources = useMemo(
    () =>
      model.sources.map((s, i) => {
        const ang = (i / Math.max(1, model.sources.length)) * Math.PI * 2 - Math.PI / 2;
        return { ...s, x: Math.cos(ang) * rimR, y: Math.sin(ang) * rimR, ang };
      }),
    [model.sources],
  );

  const hubColor =
    model.gatewayUp === true ? 'var(--accent, #22c55e)'
    : model.gatewayUp === false ? 'var(--danger, #ef4444)'
    : 'var(--text3, #87919d)';

  const activeRecoveries = sources.filter((s) => s.recovering);

  return (
    <svg
      width={size}
      height={size}
      viewBox={`${-VIEW / 2} ${-VIEW / 2} ${VIEW} ${VIEW}`}
      className={className}
      role="img"
      aria-label={`System radar — ${model.services.length} services, ${model.sources.length} sources${model.gatewayUp === false ? ', gateway offline' : ''}`}
      style={{ display: 'block', maxWidth: '100%', height: 'auto', overflow: 'visible' }}
    >
      <defs>
        <radialGradient id="aura-radar-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="var(--accent, #22c55e)" stopOpacity="0.10" />
          <stop offset="70%" stopColor="var(--accent, #22c55e)" stopOpacity="0.02" />
          <stop offset="100%" stopColor="var(--accent, #22c55e)" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="aura-radar-sweep" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="var(--accent, #22c55e)" stopOpacity="0" />
          <stop offset="100%" stopColor="var(--accent, #22c55e)" stopOpacity="0.22" />
        </linearGradient>
      </defs>

      {/* Ambient glow */}
      <circle cx={0} cy={0} r={98} fill="url(#aura-radar-glow)" />

      {/* Concentric range rings */}
      {[34, 62, 92].map((r) => (
        <circle
          key={r}
          cx={0}
          cy={0}
          r={r}
          fill="none"
          stroke="var(--border, #1d2530)"
          strokeWidth={0.6}
          strokeDasharray={r === 92 ? '2 3' : undefined}
        />
      ))}
      {/* Cross hairs */}
      <line x1={-96} y1={0} x2={96} y2={0} stroke="var(--hair, #131a23)" strokeWidth={0.5} />
      <line x1={0} y1={-96} x2={0} y2={96} stroke="var(--hair, #131a23)" strokeWidth={0.5} />

      {/* Sweep beam — the "radar is live" signal. Idle-safe: stops when reduced. */}
      {!reduce && (
        <motion.g
          animate={{ rotate: 360 }}
          transition={{ duration: 7, ease: 'linear', repeat: Infinity }}
          style={{ transformOrigin: '0px 0px' }}
        >
          <path d={`M0 0 L96 0 A96 96 0 0 0 ${96 * Math.cos(-0.5)} ${96 * Math.sin(-0.5)} Z`} fill="url(#aura-radar-sweep)" />
          <line x1={0} y1={0} x2={96} y2={0} stroke="var(--accent, #22c55e)" strokeWidth={0.8} strokeOpacity={0.5} />
        </motion.g>
      )}

      {/* Recovery arcs — source → core, animated only while deploying. */}
      <AnimatePresence>
        {activeRecoveries.map((s) => (
          <motion.line
            key={`arc-${s.id}`}
            x1={s.x} y1={s.y} x2={0} y2={0}
            stroke="var(--accent, #22c55e)"
            strokeWidth={1.2}
            initial={{ pathLength: 0, opacity: 0 }}
            animate={reduce ? { pathLength: 1, opacity: 0.6 } : { pathLength: 1, opacity: [0.2, 0.8, 0.2] }}
            exit={{ opacity: 0 }}
            transition={reduce ? { duration: 0 } : { pathLength: { duration: 0.6 }, opacity: { duration: 1.6, repeat: Infinity } }}
          />
        ))}
      </AnimatePresence>

      {/* Source rings on the rim */}
      <AnimatePresence>
        {sources.map((s) => {
          const color = SEVERITY_COLOR[s.severity as Severity];
          const active = SEVERITY_RANK[s.severity as Severity] >= 2;
          return (
            <motion.g
              key={`src-${s.id}`}
              initial={{ opacity: 0, scale: 0.4 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.4 }}
              transition={{ type: 'spring', stiffness: 260, damping: 22 }}
              style={{ transformOrigin: `${s.x}px ${s.y}px` }}
            >
              {/* Pulse halo when severity is elevated */}
              {active && !reduce && (
                <motion.circle
                  cx={s.x} cy={s.y} r={7}
                  fill="none" stroke={color} strokeWidth={1}
                  animate={{ r: [7, 13], opacity: [0.7, 0] }}
                  transition={{ duration: s.severity === 'critical' ? 1.1 : 1.8, repeat: Infinity, ease: 'easeOut' }}
                />
              )}
              <circle cx={s.x} cy={s.y} r={4.5} fill={color} fillOpacity={s.severity === 'none' ? 0.35 : 0.9} />
            </motion.g>
          );
        })}
      </AnimatePresence>

      {/* Links: hub → each service */}
      {nodes.map((n) => (
        <line
          key={`link-${n.id}`}
          x1={0} y1={0} x2={n.x} y2={n.y}
          stroke="var(--border, #1d2530)"
          strokeWidth={0.5}
          strokeOpacity={0.7}
        />
      ))}

      {/* Service nodes */}
      <AnimatePresence>
        {nodes.map((n) => {
          const nodeColor =
            n.up === true ? 'var(--accent, #22c55e)'
            : n.up === false ? 'var(--danger, #ef4444)'
            : 'var(--text3, #87919d)';
          return (
            <motion.g
              key={`node-${n.id}`}
              initial={{ opacity: 0, scale: 0 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0 }}
              transition={{ type: 'spring', stiffness: 300, damping: 24 }}
              style={{ cursor: onServiceClick ? 'pointer' : 'default', transformOrigin: `${n.x}px ${n.y}px` }}
              onClick={onServiceClick ? () => onServiceClick(n.id) : undefined}
            >
              <circle cx={n.x} cy={n.y} r={6} fill="var(--surface, #0f141b)" stroke={nodeColor} strokeWidth={1.4} />
              <circle cx={n.x} cy={n.y} r={2.4} fill={nodeColor} fillOpacity={n.up === null ? 0.4 : 1} />
            </motion.g>
          );
        })}
      </AnimatePresence>

      {/* Central hub — AURA core */}
      <g>
        {!reduce && model.gatewayUp === true && (
          <motion.circle
            cx={0} cy={0} r={11}
            fill="none" stroke={hubColor} strokeWidth={1}
            animate={{ r: [11, 17], opacity: [0.6, 0] }}
            transition={{ duration: 2.4, repeat: Infinity, ease: 'easeOut' }}
          />
        )}
        <circle cx={0} cy={0} r={10} fill="var(--surface, #0f141b)" stroke={hubColor} strokeWidth={1.6} />
        <text
          x={0} y={0}
          textAnchor="middle" dominantBaseline="central"
          fontSize={6.5} fontWeight={700}
          fill={hubColor}
          style={{ fontFamily: 'var(--font-mono, monospace)', letterSpacing: '0.04em' }}
        >
          {model.core}
        </text>
      </g>
    </svg>
  );
}

export default SystemRadar;
