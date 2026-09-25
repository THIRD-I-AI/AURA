import { describe, expect, it } from 'vitest';

import {
  LEDGER_DATASETS,
  MAX_LEDGER_ROWS,
  parseCsv,
  parseLedgerFile,
  suggestMapping,
  toNumber,
} from '../ledgerFiles';

const spec = (key: string) => LEDGER_DATASETS.find((d) => d.key === key)!;

describe('parseCsv', () => {
  it('handles quoted commas, doubled quotes, embedded newlines and CRLF', () => {
    const csv = 'id,memo\r\n1,"a, b"\r\n2,"say ""hi"""\r\n3,"line1\nline2"\r\n';
    expect(parseCsv(csv)).toEqual([
      ['id', 'memo'],
      ['1', 'a, b'],
      ['2', 'say "hi"'],
      ['3', 'line1\nline2'],
    ]);
  });

  it('strips a UTF-8 BOM and skips blank lines', () => {
    expect(parseCsv('﻿a,b\n\n1,2\n')).toEqual([['a', 'b'], ['1', '2']]);
  });

  it('keeps a final row with no trailing newline', () => {
    expect(parseCsv('a,b\n1,2')).toEqual([['a', 'b'], ['1', '2']]);
  });
});

describe('toNumber', () => {
  it.each([
    ['1234.5', 1234.5],
    ['1,234.50', 1234.5],
    ['$1,234.50', 1234.5],
    ['(500.00)', -500],
    ['-3', -3],
    ['  7 ', 7],
    ['1e3', 1000],
  ])('reads %s as %s', (raw, expected) => {
    expect(toNumber(raw)).toBe(expected);
  });

  it.each(['', 'abc', '12abc', 'NaN', 'Infinity', '1.2.3', '--4'])('rejects %j', (raw) => {
    expect(toNumber(raw)).toBeNull();
  });

  it('passes finite numbers through and rejects non-finite ones', () => {
    expect(toNumber(42)).toBe(42);
    expect(toNumber(Number.NaN)).toBeNull();
    expect(toNumber(null)).toBeNull();
  });
});

describe('parseLedgerFile — CSV', () => {
  it('turns rows into objects, coerces amount, and omits blank cells', () => {
    const r = parseLedgerFile(
      spec('ledger'), 'gl.csv',
      'internal_id,account_code,amount,memo\nL-1,4000,"1,250.00",\nL-2,5000,(75.5),rent\n',
    );
    expect(r.errors).toEqual([]);
    expect(r.columns).toEqual(['internal_id', 'account_code', 'amount', 'memo']);
    expect(r.rows).toEqual([
      { internal_id: 'L-1', account_code: '4000', amount: 1250 },
      { internal_id: 'L-2', account_code: '5000', amount: -75.5, memo: 'rent' },
    ]);
    expect(r.warnings).toEqual([]);
  });

  it('keeps identifier-looking columns as strings (leading zeros survive)', () => {
    const r = parseLedgerFile(spec('invoices'), 'inv.csv', 'invoice_number,po_number,amount\n00042,PO-7,10\n');
    expect(r.rows[0]).toMatchObject({ invoice_number: '00042', po_number: 'PO-7', amount: 10 });
  });

  it('rejects a non-numeric amount with the row and value, and returns no rows', () => {
    const r = parseLedgerFile(spec('ledger'), 'gl.csv', 'internal_id,account_code,amount\nL-1,4000,12\nL-2,4000,twelve\n');
    expect(r.errors).toHaveLength(1);
    expect(r.errors[0]).toMatch(/row 2: amount "twelve"/);
    expect(r.rows).toEqual([]);
  });

  it('truncates a long list of bad values and still reports the total', () => {
    const body = Array.from({ length: 6 }, (_, i) => `L-${i},4000,bad${i}`).join('\n');
    const r = parseLedgerFile(spec('ledger'), 'gl.csv', `internal_id,account_code,amount\n${body}\n`);
    expect(r.errors[0]).toMatch(/^6 non-numeric values/);
    expect(r.errors[0]).toMatch(/…/);
  });

  it('warns, without refusing, when a recommended column is absent', () => {
    const r = parseLedgerFile(spec('invoices'), 'inv.csv', 'invoice_number,amount\nI-1,5\n');
    expect(r.errors).toEqual([]);
    expect(r.rows).toHaveLength(1);
    expect(r.warnings[0]).toMatch(/No po_number column/);
    expect(r.warnings[0]).toMatch(/missing that value/);
  });

  it.each([
    ['', /empty/i],
    ['a,b\n', /no data rows/i],
    ['a,,c\n1,2,3\n', /empty column name/i],
    ['a,a\n1,2\n', /Duplicate column name\(s\): a/],
  ])('rejects %j', (text, message) => {
    const r = parseLedgerFile(spec('ledger'), 'gl.csv', text);
    expect(r.errors[0]).toMatch(message);
    expect(r.rows).toEqual([]);
  });

  it('refuses a file over the row limit rather than freezing the tab', () => {
    const body = Array.from({ length: MAX_LEDGER_ROWS + 1 }, () => 'x,1').join('\n');
    const r = parseLedgerFile(spec('ledger'), 'gl.csv', `internal_id,amount\n${body}\n`);
    expect(r.errors[0]).toMatch(/exceeds the 50,000-row limit/);
  });
});

describe('parseLedgerFile — JSON', () => {
  it('accepts a bare array and coerces string amounts', () => {
    const r = parseLedgerFile(spec('ledger'), 'gl.json', JSON.stringify([{ internal_id: 'L-1', account_code: '4000', amount: '9.5' }]));
    expect(r.errors).toEqual([]);
    expect(r.rows).toEqual([{ internal_id: 'L-1', account_code: '4000', amount: 9.5 }]);
  });

  it('accepts an object holding the array under the dataset key', () => {
    const r = parseLedgerFile(spec('invoices'), 'all.json', JSON.stringify({ invoices: [{ invoice_number: 'I-1', po_number: 'P-1', amount: 1 }], ledger: [] }));
    expect(r.rows).toHaveLength(1);
  });

  it.each([
    ['{not json', /Not valid JSON/],
    ['{"a":1}', /Expected a JSON array/],
    ['[1,2]', /Every JSON row must be an object/],
    ['[]', /no data rows/i],
  ])('rejects %j', (text, message) => {
    const r = parseLedgerFile(spec('ledger'), 'gl.json', text);
    expect(r.errors[0]).toMatch(message);
    expect(r.rows).toEqual([]);
  });
});

describe('column mapping (BUG-167)', () => {
  const csv = 'Ref,Acct,Amt\nL-1,4000,12\n';

  it('renames mapped columns to backend fields and clears the missing-column warning', () => {
    const r = parseLedgerFile(spec('ledger'), 'gl.csv', csv, { internal_id: 'Ref', account_code: 'Acct', amount: 'Amt' });
    expect(r.errors).toEqual([]);
    expect(r.rows).toEqual([{ internal_id: 'L-1', account_code: '4000', amount: 12 }]);
    expect(r.columns).toEqual(['internal_id', 'account_code', 'amount']);
    expect(r.sourceColumns).toEqual(['Ref', 'Acct', 'Amt']);
    expect(r.warnings).toEqual([]);
  });

  it('coerces a mapped numeric column and rejects bad values under the backend name', () => {
    const r = parseLedgerFile(spec('ledger'), 'gl.csv', 'Ref,Acct,Amt\nL-1,4000,x\n', { amount: 'Amt' });
    expect(r.errors[0]).toContain('amount "x"');
  });

  it('never overrides a column the file already names, and ignores unknown sources', () => {
    const r = parseLedgerFile(spec('ledger'), 'gl.csv', 'internal_id,account_code,amount,Other\nL-1,4000,5,zzz\n', {
      amount: 'Other', internal_id: 'Nope',
    });
    expect(r.rows[0]).toMatchObject({ amount: 5, internal_id: 'L-1', Other: 'zzz' });
  });

  it('suggests only exact header matches ignoring case and punctuation', () => {
    expect(suggestMapping(spec('invoices'), ['Invoice Number', 'PO-Number', 'Total'])).toEqual({
      invoice_number: 'Invoice Number', po_number: 'PO-Number',
    });
  });

  it('does not suggest for fields already present or for near-misses', () => {
    expect(suggestMapping(spec('invoices'), ['invoice_number', 'PO', 'amount'])).toEqual({});
  });

  it('works for JSON rows too', () => {
    const r = parseLedgerFile(spec('purchase_orders'), 'po.json', JSON.stringify([{ 'PO No': 'P-1' }]), { po_number: 'PO No' });
    expect(r.rows).toEqual([{ po_number: 'P-1' }]);
  });
});
