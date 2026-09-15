/**
 * Avatar — circular identity glyph. Deliberately round (not the house
 * rounded-none) — an avatar reads as a person, not a data surface, and a
 * circle is the universal convention users already recognize.
 *
 * Color is deterministic per identity (hashed from email/name/id), drawn
 * only from token-bound hues already in tokens.css (signal/info/blue/
 * purple/cyan) — never a raw hex — so two different users get visibly
 * different, but still on-brand, avatars.
 */
import { cn } from '@/lib/cn';

const HUES = ['signal', 'info', 'blue', 'purple', 'cyan'] as const;
type Hue = (typeof HUES)[number];

const HUE_CLASSES: Record<Hue, string> = {
  signal: 'bg-signal/15 text-signal border-signal/40',
  info: 'bg-info/15 text-info border-info/40',
  blue: 'bg-blue/15 text-blue border-blue/40',
  purple: 'bg-purple/15 text-purple border-purple/40',
  cyan: 'bg-cyan/15 text-cyan border-cyan/40',
};

const SIZE_CLASSES = {
  sm: 'size-8 text-2xs',
  md: 'size-10 text-xs',
  lg: 'size-12 text-base',
} as const;

function hueFor(seed: string): Hue {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return HUES[h % HUES.length];
}

export function initials(name?: string, email?: string): string {
  const source = (name || email || '').trim();
  if (!source) return 'AU';
  const parts = source.split(/[\s@.]+/).filter(Boolean);
  const letters = (parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '');
  return (letters || source[0]).toUpperCase();
}

export interface AvatarProps {
  name?: string;
  email?: string;
  /** Extra uniqueness key (e.g. user id) so the color stays stable even
   *  when name/email are both absent or shared. */
  seed?: string;
  size?: keyof typeof SIZE_CLASSES;
  className?: string;
}

export function Avatar({ name, email, seed, size = 'md', className }: AvatarProps) {
  const key = seed || email || name || 'AU';
  return (
    <div
      className={cn(
        'grid shrink-0 place-items-center rounded-full border font-mono font-bold leading-none',
        SIZE_CLASSES[size],
        HUE_CLASSES[hueFor(key)],
        className,
      )}
    >
      {initials(name, email)}
    </div>
  );
}
