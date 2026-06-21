export function fuzzyScore(haystack: string, needle: string): number {
  if (!needle) return 1;
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase();
  if (h === n) return 100;
  if (h.startsWith(n)) return 80;
  if (h.includes(n)) return 60;
  let last = -1;
  let runs = 0;
  for (const ch of n) {
    const idx = h.indexOf(ch, last + 1);
    if (idx === -1) return 0;
    if (idx === last + 1) runs += 1;
    last = idx;
  }
  return Math.max(1, 30 + runs * 2 - (h.length - n.length) / 4);
}
