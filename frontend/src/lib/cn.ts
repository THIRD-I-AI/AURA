import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * cn — merge conditional class lists and resolve Tailwind conflicts.
 * Standard shadcn/ui helper: clsx builds the list, twMerge dedupes so the
 * last-wins rule holds for conflicting utilities (e.g. `px-2` + `px-4` → `px-4`).
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
