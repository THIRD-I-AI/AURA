/* Bring-your-own-ledger file parsing for the financial audit (BUG-158).

   Turns a CSV or JSON file into the array-of-objects the gateway's
   POST /audit/financial expects for one dataset. The backend takes free-form
   dicts and reads a known set of keys (see FinancialAuditorAgent), so:
     - blocking `errors` are things that would corrupt or break the audit
       (unreadable file, no rows, a non-numeric value in a numeric column,
       too many rows);
     - advisory `warnings` are recommended columns that are absent — the
       backend reads every key with .get(), so a missing column doesn't crash
       but reads as a missing value (a missing po_number on invoices reads as
       "no purchase order"), so we say so without refusing the file. */

export type LedgerDatasetKey =
  | 'ledger'
  | 'invoices'
  | 'purchase_orders'
  | 'journal_entries'
  | 'goods_receipts'
  | 'historical_reports';

export interface LedgerDatasetSpec {
  key: LedgerDatasetKey;
  label: string;
  /** Columns the audit procedures read for this dataset. */
  recommended: string[];
  /** Columns that must hold numbers; coerced from text, rejected if they can't be. */
  numeric: string[];
}

export const LEDGER_DATASETS: LedgerDatasetSpec[] = [
  { key: 'ledger', label: 'General ledger', recommended: ['internal_id', 'account_code', 'amount'], numeric: ['amount'] },
  { key: 'invoices', label: 'Invoices', recommended: ['invoice_number', 'po_number', 'amount'], numeric: ['amount'] },
  { key: 'purchase_orders', label: 'Purchase orders', recommended: ['po_number'], numeric: ['amount', 'approval_limit'] },
  { key: 'journal_entries', label: 'Journal entries', recommended: ['internal_id', 'account_code', 'amount'], numeric: ['amount'] },
  { key: 'goods_receipts', label: 'Goods receipts', recommended: ['po_number'], numeric: ['amount'] },
  { key: 'historical_reports', label: 'Historical reports', recommended: ['account_code', 'amount'], numeric: ['amount'] },
];

export const MAX_LEDGER_ROWS = 50_000;
const MAX_LISTED_ERRORS = 3;

export interface ParsedLedgerFile {
  rows: Record<string, unknown>[];
  columns: string[];
  errors: string[];
  warnings: string[];
}

/** RFC 4180 CSV: quoted fields may hold commas, doubled quotes and newlines. */
export function parseCsv(text: string): string[][] {
  const src = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  const rows: string[][] = [];
  let row: string[] = [];
  let cur = '';
  let inQuotes = false;
  const endField = () => { row.push(cur); cur = ''; };
  const endRow = () => {
    endField();
    if (row.some((c) => c.trim() !== '')) rows.push(row);
    row = [];
  };
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (inQuotes) {
      if (ch === '"') {
        if (src[i + 1] === '"') { cur += '"'; i++; } else { inQuotes = false; }
      } else { cur += ch; }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      endField();
    } else if (ch === '\r') {
      if (src[i + 1] === '\n') i++;
      endRow();
    } else if (ch === '\n') {
      endRow();
    } else {
      cur += ch;
    }
  }
  if (cur !== '' || row.length > 0) endRow();
  return rows;
}

/** "1,234.50", "$1,234.50", "(500.00)" (accounting negative) -> number; else null. */
export function toNumber(raw: unknown): number | null {
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
  if (typeof raw !== 'string') return null;
  let s = raw.trim();
  if (s === '') return null;
  let negative = false;
  if (/^\(.*\)$/.test(s)) { negative = true; s = s.slice(1, -1); }
  s = s.replace(/^[$€£]/, '').replace(/,/g, '').trim();
  if (!/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(s)) return null;
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  return negative ? -n : n;
}

function fail(errors: string[], columns: string[] = []): ParsedLedgerFile {
  return { rows: [], columns, errors, warnings: [] };
}

export function parseLedgerFile(spec: LedgerDatasetSpec, filename: string, text: string): ParsedLedgerFile {
  let raw: Record<string, unknown>[];
  let columns: string[];

  if (/\.json$/i.test(filename)) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      return fail([`Not valid JSON: ${e instanceof Error ? e.message : String(e)}`]);
    }
    // Accept the bare array or an object holding it under this dataset's key.
    const list = Array.isArray(parsed)
      ? parsed
      : parsed !== null && typeof parsed === 'object' && Array.isArray((parsed as Record<string, unknown>)[spec.key])
        ? (parsed as Record<string, unknown[]>)[spec.key]
        : null;
    if (!list) return fail([`Expected a JSON array of rows (or an object with a "${spec.key}" array).`]);
    if (!list.every((r) => r !== null && typeof r === 'object' && !Array.isArray(r))) {
      return fail(['Every JSON row must be an object of column: value pairs.']);
    }
    raw = list as Record<string, unknown>[];
    columns = [...new Set(raw.flatMap((r) => Object.keys(r)))];
  } else {
    const table = parseCsv(text);
    if (table.length === 0) return fail(['The file is empty.']);
    const header = table[0].map((h) => h.trim());
    if (header.some((h) => h === '')) return fail(['The header row has an empty column name.']);
    const dupes = header.filter((h, i) => header.indexOf(h) !== i);
    if (dupes.length > 0) return fail([`Duplicate column name(s): ${[...new Set(dupes)].join(', ')}.`], header);
    columns = header;
    raw = table.slice(1).map((cells) => {
      const obj: Record<string, unknown> = {};
      header.forEach((h, i) => {
        const v = (cells[i] ?? '').trim();
        // A blank cell means "not provided": omit the key rather than send "".
        if (v !== '') obj[h] = v;
      });
      return obj;
    });
  }

  if (raw.length === 0) return fail(['The file has a header but no data rows.'], columns);
  if (raw.length > MAX_LEDGER_ROWS) {
    return fail([`${raw.length.toLocaleString()} rows exceeds the ${MAX_LEDGER_ROWS.toLocaleString()}-row limit for one file.`], columns);
  }

  const errors: string[] = [];
  const bad: string[] = [];
  const rows = raw.map((r, idx) => {
    const out: Record<string, unknown> = { ...r };
    for (const col of spec.numeric) {
      if (!(col in out)) continue;
      const n = toNumber(out[col]);
      if (n === null) bad.push(`row ${idx + 1}: ${col} "${String(out[col])}"`);
      else out[col] = n;
    }
    return out;
  });
  if (bad.length > 0) {
    const shown = bad.slice(0, MAX_LISTED_ERRORS).join('; ');
    errors.push(`${bad.length} non-numeric value${bad.length === 1 ? '' : 's'} in numeric columns (${shown}${bad.length > MAX_LISTED_ERRORS ? '; …' : ''}).`);
  }

  const warnings: string[] = [];
  const missing = spec.recommended.filter((c) => !columns.includes(c));
  if (missing.length > 0) {
    warnings.push(`No ${missing.join(', ')} column — the audit checks that read it will see every row as missing that value, which can change or add findings.`);
  }
  return { rows: errors.length > 0 ? [] : rows, columns, errors, warnings };
}
