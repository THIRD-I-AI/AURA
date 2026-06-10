# S32 — DPC: Dual-Paradigm SQL Verification (design)

**Status:** approved 2026-05-31 · Owner: Mounith · Branch: `feature/s32-dpc-sql-verification`
**Anchor:** DPC (dual-paradigm consistency) — independence-based verification of LLM-generated SQL by cross-checking against an independently-generated Pandas solution.

## Problem

AURA's chat/analytics path turns a natural-language question into SQL with a single
LLM pass (`SQLGeneratorAgent`), validates it with `EXPLAIN`, and runs it. `EXPLAIN`
proves the SQL is *syntactically valid and plannable* — it says **nothing about whether
the SQL answers the question**. A hallucinated `JOIN`, a wrong `GROUP BY`, a flipped
filter, `SUM` where the user meant `COUNT` — all pass `EXPLAIN` and return a confident,
wrong number. For a product whose whole thesis is "an answer an expert can't dismiss,"
shipping an unverified number is the core failure mode.

DPC closes this: after the SQL runs, independently re-derive the same answer in a
**different paradigm** (Pandas), and compare. Cross-paradigm disagreement catches errors
that a second SQL sample would share. Agreement is positive evidence the answer is real.

## Goal

After `SQLGeneratorAgent` generates + executes SQL, run an independent Pandas computation
of the same question over the same data and cross-check the results. Surface a **tri-state**
verdict on the agent result:

- `verified` — the two paradigms agree → high confidence.
- `mismatch` — they disagree → the SQL is suspect; attempt **one** bounded SQL
  regeneration with the discrepancy as feedback, then surface the verdict honestly.
- `skipped` — could not cross-check (multi-table query, dataset too large, LLM
  unavailable/timed-out, eval error) → say so plainly; never a false "✓".

The verdict is best-effort and **never blocks or breaks** the main SQL path (the
critic-timeout lesson: a slow/rate-limited LLM must not stall the user's answer).

## Scope (v1) — deliberate cuts

DPC v1 verifies **single-table, bounded-size** queries — the dominant uploaded-CSV
analytics case. Everything else degrades to an honest `skipped`:

- **Single base table only.** Extract referenced base tables via `sqlglot` (already a
  dependency). If the executed SQL references ≠ 1 distinct base table (joins, set ops,
  multiple subquery sources) → `skipped: "multi-table queries not yet cross-verified"`.
  *(Upgrade path: materialize each table + let the Pandas solver join — deferred.)*
- **Bounded source.** Materialize the one table via `SELECT * FROM "<table>"` through the
  existing `execute_sql` tool. If its row count exceeds `AURA_DPC_MAX_ROWS` (default
  `200000`) → `skipped: "dataset too large to cross-verify"`.
- **Single Pandas expression**, not statements. The solver returns one expression over a
  DataFrame named `df`; we `eval` it. No multi-line code, no imports, no I/O.
- **Value-multiset comparison**, not Bipartite Soft-F1. We compare the *flattened, sorted,
  tolerance-rounded multiset of scalar values* from each result. This is robust to column
  labelling and row ordering and catches the dominant error modes (wrong number / count /
  filter). Known looseness: a coincidental permutation of multi-column results could
  false-agree. *(Upgrade path: bipartite soft-F1 — deferred.)*

Non-goals: no full Python-sandbox service; no change to the audit path (DPC is for the
chat/text-to-SQL path only); no multi-table joins in Pandas; no statement execution.

## Architecture

One new module, `aurabackend/agents/dpc_verifier.py`, with small, independently-testable
units. `SQLGeneratorAgent._run` calls it after execution. The verifier depends only on the
tool registry (to materialize the table) and the LLM provider (to write the Pandas) — no
agent internals — so it is testable in isolation by injecting a fake `llm` and `tools`.

```
SQLGeneratorAgent._run:
  generate SQL → EXPLAIN-validate → execute (columns, rows)
       │
       └─ if executed and AURA_DPC_ENABLED:
            verify_sql_result(question, sql, columns, rows, tools, llm, timeout=…)
                 │  (one bounded pass, returns VerificationResult)
                 ├─ extract_single_table(sql)            → table or None→skipped
                 ├─ materialize_table(table, tools, max)  → df or None→skipped
                 ├─ generate_pandas_solution(q, df, llm)  → "df.…"  (bounded, threaded)
                 ├─ safe_eval_pandas(expr, df)            → result (denylist+nobuiltins+timeout)
                 └─ results_agree(columns, rows, result)  → verified | mismatch
            if mismatch and retries_left:
                 regenerate SQL with the discrepancy as a hint → re-execute → re-verify
       │
       └─ result.output += {"cross_verified": Optional[bool], "verification": {…}}
```

### Components in `dpc_verifier.py`

- `VerificationResult` (pydantic `BaseModel`): `status: Literal["verified","mismatch","skipped"]`,
  `verified: Optional[bool]` (`True`/`False`/`None`), `reason: str`, `pandas_expr: Optional[str]`,
  `method: str = "dual_paradigm_pandas"`.
- `extract_single_table(sql) -> Optional[str]` — parse with `sqlglot` (postgres dialect);
  collect distinct `exp.Table` names; return the lone base table or `None` (0, >1, or
  parse failure). Strips quoting.
- `async materialize_table(table, tools, max_rows) -> Optional[pd.DataFrame]` — call
  `execute_sql` with `SELECT * FROM "<table>"`; build `pd.DataFrame(rows, columns=columns)`;
  return `None` if the result exceeds `max_rows` or the fetch fails. Identifier is the
  `sqlglot`-extracted table name re-quoted — never raw user text.
- `generate_pandas_solution(question, df, llm) -> Optional[str]` — prompt the LLM with the
  question + the DataFrame's columns/dtypes (a few sample rows) for **one Pandas expression
  over `df`**; strip fences; return the expression. Sync `llm.generate` — the caller runs it
  under a thread + timeout.
- `safe_eval_pandas(expr, df) -> Any` — **defense in depth**: (1) denylist pre-check rejects
  any expression containing `__`, `import`, `open`, `exec`, `eval`, `os`, `sys`,
  `subprocess`, `globals`, `locals`, `getattr`, `setattr`, `compile`, `input`, `breakpoint`;
  (2) `eval(expr, {"__builtins__": {}}, {"df": df, "pd": pd, "np": np})`; (3) the whole eval
  runs under a thread-join timeout. Raises on denylist hit / eval error / timeout.
- `results_agree(sql_columns, sql_rows, pandas_result, tol=1e-6) -> bool` — normalize both
  sides to a sorted multiset of scalar values (numbers → `round(float, 6)`; `None`/`NaN` →
  a sentinel; everything else → `str`); compare the multisets. Pandas scalar → `[[v]]`;
  Series → one column; DataFrame → its values.
- `async verify_sql_result(question, sql, sql_columns, sql_rows, tools, llm, *, timeout, max_rows, tol) -> VerificationResult`
  — orchestrates the above for **one** pass; wraps the LLM gen + eval in
  `asyncio.wait_for(asyncio.to_thread(...), timeout)`; any exception/timeout →
  `skipped` with the reason (`type(exc).__name__`). Never raises.

### Integration into `SQLGeneratorAgent._run`

After the existing execute block, when `exec_result` is present and `AURA_DPC_ENABLED != "0"`:

1. Extract `columns`/`rows` from `exec_result` (dict or model).
2. `vr = await verify_sql_result(ctx.task_description, sql, columns, rows, self.tools, self._llm, timeout=_dpc_timeout(), max_rows=_dpc_max_rows())`.
3. If `vr.status == "mismatch"` and one retry remains: call `_generate_sql` again with a
   discrepancy hint appended to the question (`"The previous SQL `…` disagreed with an
   independent computation; reconsider the aggregation/filter."`), `_sanitise`, re-`EXPLAIN`,
   re-execute, re-verify once. Keep whichever result ends `verified`; otherwise keep the
   original SQL result with the final (mismatch) verdict.
4. Attach to `result.output`: `"cross_verified": vr.verified`, `"verification":
   {"status", "reason", "pandas_expr", "method"}`. Add a `result.add_step` for visibility.

The retry loop lives in `_run` (it needs the agent's `_generate_sql`/`_sanitise`/execute);
the verifier stays a pure function of `(tools, llm, sql, rows)`.

### Config

- `AURA_DPC_ENABLED` (default `"1"`) — master switch; `"0"` disables entirely (byte-identical
  to today's behaviour — the chat path adds nothing).
- `AURA_DPC_TIMEOUT_S` (default `"10"`) — wall-clock bound on the Pandas gen + eval pass.
- `AURA_DPC_MAX_ROWS` (default `"200000"`) — source-table materialization ceiling.
- `AURA_DPC_MAX_RETRIES` (default `"1"`) — bounded SQL regenerations on mismatch.

## Error handling

- **Every** failure mode resolves to a `VerificationResult`, never an exception out of
  `verify_sql_result`. Timeout, LLM unavailable, denylist hit, eval error, parse failure,
  oversized table → `skipped` with a specific `reason`.
- A `mismatch` is *not* an error — it's the feature working. It triggers the bounded retry
  and an honest verdict.
- DPC failure never changes `result.status`: the user still gets their SQL answer. DPC only
  *annotates* it. (If `AURA_DPC_ENABLED="0"`, no annotation at all.)

## Testing (Tier A + Tier B)

**Tier A — pure Python, no optional deps, deterministic (inject a fake `llm`/`tools`):**
- `extract_single_table`: single-table SELECT → the table; 2-table JOIN → `None`;
  `UNION` of two tables → `None`; unparseable → `None`; quoted/schema-qualified names.
- `safe_eval_pandas`: `df["y"].sum()` → correct value; rejects `__import__("os")`,
  `import os`, `open("x")`, `df.__class__`, `globals()`; a deliberately slow callable times
  out (timeout mechanism tested via an injected sleep, not a real infinite loop).
- `results_agree`: equal numbers → `True`; off-by-one → `False`; within `tol` → `True`;
  row-permuted equal sets → `True`; scalar vs 1×1 frame → `True`; `None`/`NaN` handling.
- `verify_sql_result` with a **fake llm** returning a known-correct expr and a **fake tools**
  returning a fixed table: agreeing case → `verified`; fake llm returning a wrong expr →
  `mismatch`; fake tools reporting an oversized table → `skipped`; multi-table SQL → `skipped`.
- `SQLGeneratorAgent` integration with fakes: a question whose SQL agrees →
  `output["cross_verified"] is True`; a forced mismatch with retries exhausted →
  `cross_verified is False` + `verification.status == "mismatch"`; `AURA_DPC_ENABLED="0"`
  → no `verification` key (today's behaviour preserved).

**Tier B — real LLM (gated, dedicated CI lane / `AURA_LLM_*` env):**
- End-to-end: a correct NL→SQL question over a small fixture table verifies `verified`.
- An intentionally wrong SQL (handed in) over the same table → the real cross-check returns
  `mismatch` (proves the independent paradigm actually catches a real error).

CI: add `test_dpc_verifier.py` to the base lane (Tier A). The Tier-B end-to-end goes on the
existing real-LLM eval-gate lane (or a `skipif` on LLM availability), per
`feedback_optional_dep_test_gating`.

## Security

Threat model: non-adversarial LLM (it's trying to answer, not attack), **local/VPC,
single-tenant, user's own data**. Mitigations, defense-in-depth: (1) source identifier is
the `sqlglot`-extracted table name, re-quoted — never raw user input concatenated into SQL;
(2) the Pandas expression is denylisted (`__`, `import`, `os`, `getattr`, …) **and** eval'd
with `__builtins__` stripped and only `{df, pd, np}` in scope; (3) a wall-clock timeout
bounds runaway evals. This is intentionally lighter than a full Python sandbox service,
which is over-engineering for this threat model (recorded in the design discussion).

## Rollback

`AURA_DPC_ENABLED="0"` fully disables DPC at runtime with zero behavioural change to the
existing SQL path — the safety valve if the cross-check proves noisy or costly in practice.
