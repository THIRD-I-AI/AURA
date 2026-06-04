# Auto-Encode Categorical Columns in /audit — Design Spec

**Date:** 2026-06-03
**Track:** backend follow-up to S31d/S31e (audit-your-own-data) · branch `feature/audit-auto-encode`
**Owner:** Rohith (full-stack)
**Closes:** #49

---

## 1. Goal

Make `/audit` ("audit your own data") work on **real-world CSVs that contain
categorical string columns** (e.g. ProPublica COMPAS `race`, `sex`,
`c_charge_degree`) without the user pre-encoding anything. Today every mapped
column is coerced with `pd.to_numeric(errors="coerce")` then `dropna()`, so any
string column → all NaN → **all rows dropped** → `"0 usable rows"`. This makes
the headline "upload your own data" demo brittle (verified: raw COMPAS `race`
drops to 0 rows; it only ran after manual 0/1 encoding).

After this change, a user uploads a raw CSV, maps columns, and the audit
auto-encodes categoricals — surfacing every transform in the certificate.

## 2. Where this lives

`aurabackend/counterfactual_service/audit_mapping.py` — inside
`validate_and_prepare`, **before** the numeric coercion. Because the auto-DAG and
identification statement are built from the confounder/treatment **names**
(`build_query_from_mapping` → `build_dag_from_mapping`, `identification_statement`),
encoding must also **rewrite the effective mapping** to the new column names.

**Signature change:**
```python
# before
def validate_and_prepare(df, mapping) -> Tuple[pd.DataFrame, DataQuality]
# after
def validate_and_prepare(df, mapping) -> Tuple[pd.DataFrame, DataQuality, dict]
#                                                                          ^ effective_mapping
```
`effective_mapping` is a copy of `mapping` with `treatment`/`confounders`
(and `instrument`) replaced by their encoded column names. When nothing needs
encoding it equals the input mapping (back-compat for numeric inputs).

**Callers to update:**
- `audit_worker.run_audit_subprocess` (`audit_worker.py:40`):
  ```python
  clean_df, dq, eff = validate_and_prepare(df, payload)
  query = build_query_from_mapping(clean_df, eff)
  result["identification"] = identification_statement(eff)
  ```
- Any `/audit` endpoint pre-validate call + existing tests that unpack two values
  → update to three (or `clean_df, dq, _ = …`).

## 3. Encoding rules

Applied per mapped role, on a working copy, before coercion. `CARD_CAP = 12`.

### 3.1 Treatment — must be binary (the 0/1 causal contrast)
- Already `{0,1}` numeric → unchanged.
- Exactly **2 distinct** values (string or numeric) → map sorted-first → `0`,
  other → `1`; warning: `treatment 'race' encoded: African-American=0, Caucasian=1`.
- **>2 distinct** → **structured `ValueError`** (→ failed job / 400) with guidance:
  `treatment 'race' has 6 categories; the audit needs a binary contrast — filter
  to two groups or pick a reference.` (A one-vs-rest contrast is a weaker, more
  ambiguous causal claim — out of scope; the frontend S31e guard already steers
  users here.)

### 3.2 Confounders — one-hot encode categoricals
- Numeric → unchanged.
- Non-numeric with `2 ≤ distinct ≤ CARD_CAP` → **one-hot, drop-first** (avoid
  collinearity); replace the confounder name in the effective mapping with the
  dummy column names (`sex` → `sex_Male`); warning lists the dummies.
- Non-numeric with `distinct > CARD_CAP` → **`ValueError`** with guidance
  (one-hotting high-cardinality columns is leakage/nonsense; drop or bucket it).
- A binary categorical confounder is the drop-first one-hot special case → a
  single 0/1 dummy.

### 3.3 Outcome — numeric (binary allowed)
- Numeric → unchanged.
- Exactly 2 distinct (string/numeric) → 0/1; warning.
- >2 non-numeric → `ValueError` (`outcome must be numeric or binary`).

### 3.4 Instrument — same as treatment (binary encode / numeric).

### 3.5 Ordering
Encode → then the existing numeric coercion + `dropna` + min-rows check run on the
encoded frame (encoded columns are already numeric, so they survive). The existing
median-binarisation of a *continuous numeric* treatment is unchanged.

## 4. Surfacing (compliance: no silent mangling)

Every encoding/one-hot/error decision is appended to `DataQuality.warnings`
(existing field, already shown in the certificate + PDF). A reviewer can see
exactly what was transformed:
```
treatment 'race' encoded: African-American=0, Caucasian=1
confounder 'sex' one-hot encoded → sex_Male (drop-first)
confounder 'c_charge_degree' one-hot encoded → c_charge_degree_M (drop-first)
```

`identification_statement` continues to read from `effective_mapping`, so it lists
the adjusted columns actually used (the dummies). Human-readable enough; the
original names appear in the warnings.

## 5. New unit — `encode_for_audit`

Keep `validate_and_prepare` readable by extracting the encoding into one pure-ish
helper:
```python
def encode_for_audit(df: pd.DataFrame, mapping: dict, card_cap: int = 12)
    -> Tuple[pd.DataFrame, dict, list[str]]:
    """Return (encoded_df, effective_mapping, warnings). Raises ValueError on a
    >2-category treatment/outcome or a high-cardinality categorical confounder."""
```
`validate_and_prepare` calls it first, then runs its existing coercion/dropna/
min-rows/treatment-binarise logic on `encoded_df` + `effective_mapping`.

## 6. Testing (Tier A + Tier B)

**Tier A (pure pandas, always-on):**
- Binary string treatment → 0/1 + warning naming the mapping.
- `>2`-category treatment → `ValueError` with "two groups"/"reference" guidance.
- Categorical confounder (`sex` M/F) → one-hot drop-first; effective_mapping
  confounders contain `sex_Male`, not `sex`.
- Multi-category confounder (`c_charge_degree` F/M/O, ≤ cap) → dummies; original
  name gone from effective mapping.
- High-cardinality confounder (> cap distinct) → `ValueError`.
- Numeric-only inputs → effective_mapping == input, no warnings, df unchanged.
- `effective_mapping` from `validate_and_prepare` flows into
  `build_query_from_mapping` so the DAG adjusts on the dummy columns (assert DAG
  edges reference `sex_Male`).

**Tier B (gated: econml):**
- Raw COMPAS-style CSV with **string `race`** (2 groups) + a **string confounder**
  audits end-to-end through `run_audit_subprocess` → signed artifact, `n_clean`
  > 100, warnings list the encodings. (No manual pre-encoding.)

## 7. Build order

1. `encode_for_audit` + Tier A tests (treatment binary, confounder one-hot, error
   cases, numeric-passthrough).
2. Wire into `validate_and_prepare` (3-tuple return) + update callers/tests.
3. `effective_mapping` → DAG integration test.
4. Tier B end-to-end COMPAS-string test.

Each step keeps the suite green.

## 8. Non-goals (YAGNI)

- One-vs-rest / multinomial treatment contrasts (treatment stays binary).
- Ordinal-aware or target encoding (plain one-hot only).
- Imputation of missing categoricals (still dropped by the existing `dropna`).
- Auto-bucketing high-cardinality columns (error with guidance instead).
- Frontend changes — S31e already guards/guides; this makes the backend *accept*
  more, so the guard becomes a soft helper rather than a hard stop (a follow-up
  can relax the FE guard once this ships).
