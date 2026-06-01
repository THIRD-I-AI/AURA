export type ColumnType = 'number' | 'string';

export interface CsvHead {
  columns: string[];
  rows: string[][];
  types: Record<string, ColumnType>;
}

// Parse a single CSV line: double-quoted fields may contain commas and escaped
// quotes (""). Fields are trimmed. (Embedded newlines inside quotes are not
// supported — this feeds the preview UI only; the backend parses authoritatively.)
function parseLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else { inQuotes = false; }
      } else { cur += ch; }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      out.push(cur); cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

export function parseCsvHeadAndRows(text: string, maxRows = 20): CsvHead {
  const lines = text.split(/\r\n|\r|\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return { columns: [], rows: [], types: {} };
  const columns = parseLine(lines[0]);
  const rows = lines.slice(1, 1 + maxRows).map(parseLine);
  const types: Record<string, ColumnType> = {};
  columns.forEach((col, idx) => {
    const samples = rows.map((r) => r[idx]).filter((v) => v !== undefined && v !== '');
    const allNum = samples.length > 0 && samples.every((v) => !Number.isNaN(Number(v)));
    types[col] = allNum ? 'number' : 'string';
  });
  return { columns, rows, types };
}
