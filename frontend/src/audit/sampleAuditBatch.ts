/** Canned ledger with one of each finding type — the investor demo batch.
 *  Unmatched PO (AS 2201), duplicate + round-dollar JEs (AS 2401), and a
 *  >$100k variance (AS 2305). PII here exercises the egress masking. */
export const SAMPLE_AUDIT_BATCH = {
  tenant_id: 'demo-tenant',
  ledger: [{ internal_id: 'L-1001', account_code: '4000', amount: 250000.0 }],
  purchase_orders: [{ po_number: 'PO-7001' }],
  invoices: [
    { invoice_number: 'INV-9001', po_number: 'PO-MISSING', employee_name: 'Ada Lovelace', amount: 12400.5 },
    { invoice_number: 'INV-9002', po_number: 'PO-7001', employee_name: 'Grace Hopper', amount: 980.0 },
  ],
  journal_entries: [
    { internal_id: 'JE-1', amount: 5000.0, account_code: '6000', vendor_id: 'V-77' },
    { internal_id: 'JE-2', amount: 5000.0, account_code: '6000', vendor_id: 'V-77' },
  ],
};
