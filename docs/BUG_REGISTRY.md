# AURA Bug Registry

Every anomaly `scripts/verify_live_deployment.py` (or any other live/manual
testing pass) finds gets an entry here **before** any fix is attempted.
This is the process, not a suggestion:

1. File the entry the moment an anomaly is observed — status `open`, root
   cause `unconfirmed` if not yet investigated. Don't skip straight to a fix.
2. Triage: either `false-positive` (state why — bad test data, an already-
   documented exclusion, etc. — and stop) or a confirmed root cause with
   file:line citations.
3. Confirmed bugs are ranked by severity and fixed **one at a time** — one
   bug, one branch, one PR. Never bundle unrelated fixes; a bundled fix
   breaks the lineage tracking below (unclear which change caused which
   regression).
4. Before marking `fixed`, re-run the specific check that failed (not the
   whole suite) to confirm the fix actually resolves it live.
5. If a later run finds a NEW anomaly, check whether it plausibly traces to
   a recent fix (same file/endpoint/timeframe) before filing it as
   unrelated. If it does, set **Caused by** to the prior bug's ID instead
   of starting a disconnected investigation — this field is the lineage
   graph: `grep "Caused by: BUG-012"` reconstructs which fixes spawned
   which regressions, without needing separate tooling.
6. Goal state: the verification suite is green and every entry here is
   `fixed` / `false-positive` / `wontfix` with a citation — no entry left
   `open`. That is what "solid base" means, made checkable.

## Severity

- `blocks-feature` — a promised capability doesn't work at all.
- `degrades-accuracy` — it works, but gives a wrong/incomplete result.
- `cosmetic` — works correctly, presentation/wording issue only.

## Entry template

```
## BUG-<NNN>: <short title>
- **Status:** open | false-positive | fixed | wontfix
- **Found by:** <verification run date/name, e.g. "live-verify 2026-08-31">
- **Severity:** blocks-feature | degrades-accuracy | cosmetic
- **Root cause:** <one paragraph, with file:line citations>
- **Caused by:** BUG-<NNN> | none
- **Fix:** <PR link, once opened>
```

---

## Pre-existing entries (found before this registry existed, backfilled for continuity)

## BUG-001: RecoveryLoop._validate_shim false-rejects a correct schema-fix shim
- **Status:** fixed
- **Found by:** live-verify 2026-08-31 (manual, staging: https://dataaura.duckdns.org)
- **Severity:** blocks-feature (UASR schema-drift auto-heal)
- **Root cause:** `aurabackend/uasr/recovery_loop.py:358` — the schema-drift
  success check required `not post_drift.drift_detected` (zero drift of
  ANY kind), which was provably dead code: reaching that line already
  required `post_drift.drift_detected` to be True, so it could never
  diverge from the earlier check on line 339. A shim that correctly fixed
  SCHEMA drift was rejected whenever the corrected batch also carried
  unrelated statistical drift — a normal real-world co-occurrence.
- **Caused by:** none
- **Fix:** #264 (`fix/uasr-schema-validation-false-reject`)

## BUG-002: metrics.record_from_loop_result mis-attributes drift_type
- **Status:** fixed
- **Found by:** live-verify 2026-08-31 (manual, staging: https://dataaura.duckdns.org)
- **Severity:** degrades-accuracy (`GET /uasr/metrics` dashboards)
- **Root cause:** `aurabackend/uasr/metrics.py` — `record_from_loop_result`
  inferred `drift_type`/`severity` from `loop_result.diagnosis` via
  `hasattr(diag, "drift_type")`. `DiagnosisResult` (uasr/models.py) has no
  such field at all, so the check was always False and every recorded
  event silently defaulted to `STATISTICAL`/`LOW` regardless of the real
  drift — not just on failed recoveries, on every call.
- **Caused by:** none
- **Fix:** #264 (`fix/uasr-schema-validation-false-reject`, same PR as
  BUG-001 — bundled because both were found and fixed via the same live
  investigation before this one-bug-per-PR discipline was formalized;
  future entries follow the strict one-at-a-time rule above)

## BUG-003: Gateway proxies only 7 of ~20 UASR endpoints
- **Status:** fixed
- **Found by:** live-verify 2026-08-31 (manual, staging: https://dataaura.duckdns.org)
- **Severity:** blocks-feature (`/uasr/heal`, the actual self-healing
  endpoint, was unreachable from outside the box)
- **Root cause:** `aurabackend/api_gateway/routers/pipelines.py` only
  defined gateway routes for `ingest`/`baseline`/`metrics`/`drift/status`/
  `recovery/pending`/`approve`/`reject`. `/uasr/heal`, `/uasr/deployment`,
  `/uasr/correlation`, `/uasr/rollback`, `/uasr/recovery/{id}`, and others
  had no facade at all.
- **Caused by:** none
- **Fix:** #263 (`fix/uasr-gateway-proxy-gaps`), which also adds
  `tests/test_uasr_gateway_facade_coverage.py` so this class of gap fails
  CI going forward instead of silently 404ing in production.

## BUG-004: test_counterfactual_sprint13.py rate-limit flake in the full suite
- **Status:** open
- **Found by:** pre-push gate, feature/uasr-cross-source-correlation, 2026-08-31
- **Severity:** cosmetic (test infra, not product) — tracked here anyway
  because a flaky pre-push gate erodes trust in every other green run.
- **Root cause:** unconfirmed. `test_bulk_replay_returns_ndjson_with_mixed_statuses`
  failed with `{"error":"RATE_LIMITED", ...}` when the full ~2100-test
  suite ran back-to-back — plausibly the shared in-process rate limiter
  (`shared/rate_limit.py`, 100 req/60s per IP) tripping under the combined
  request volume of the whole suite hitting one TestClient IP, despite
  `conftest.py`'s `_reset_rate_limit_counters` autouse fixture. Passed on
  every other run before and after (including immediately after this
  failure, unmodified) — consistent with order/volume-dependent flakiness,
  not a deterministic bug, but not yet root-caused with a citation.
- **Caused by:** none identified yet
- **Fix:** none yet — not blocking product work, revisit if it recurs
  often enough to slow down the push workflow.
