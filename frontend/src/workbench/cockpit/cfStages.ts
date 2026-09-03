/* Forensic-audit stage copy — shared by ForensicAuditPanel (renders it) and
   Workbench.tsx (times the running-audit animation off its length). Split
   out so ForensicAuditPanel.tsx can stay component-only
   (react-refresh/only-export-components). */
export const CF_STAGES = [
  'Submitting job to counterfactual service…',
  'Estimators 1–4: backdoor.linear_reg · psm · dml · ipw…',
  'Estimators 5–7: frontdoor · iv · gcm…',
  'Refuters: placebo · random-cause · subset · unobserved-confound…',
  'Adversarial critic reviewing challenges…',
  'Conformal CI + E-value…',
  'Signing record (ED25519) → ledger…',
];
