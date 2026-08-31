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

## BUG-005: unauthenticated-by-design verification endpoints return 401 live
- **Status:** fixed
- **Found by:** live-verify 2026-08-31 (`scripts/verify_live_deployment.py`,
  first run against https://dataaura.duckdns.org — `jwks` check failed)
- **Severity:** blocks-feature — external cryptographic verification is a
  core promised capability (TRAIGA federation, S19): a third-party auditor
  is supposed to verify AURA's ED25519 signatures via `/jwks` and RFC 6962
  Merkle proofs via `/audit/sth` + `/audit/inclusion/{proof}` *without* an
  AURA login. On any deployment with auth correctly armed, none of them
  are reachable, which defeats the entire point of the feature.
- **Root cause:** `aurabackend/shared/middleware.py:116-119` —
  `_PUBLIC_PATHS` (the global `JWTAuthMiddleware`'s allowlist) never
  included `/api/v1/counterfactual/jwks`, `/api/v1/counterfactual/audit/sth`,
  or `/api/v1/counterfactual/audit/inclusion/{proof}` — despite each route
  handler being explicitly coded and commented as intentionally
  unauthenticated (`counterfactual.py`'s `jwks()`: *"Service route is
  unauthed (public key material), so this proxy takes no auth either"*).
  The existing regression tests (`test_jwks_reachable_unauthenticated`,
  `test_sth_reachable_unauthenticated`, `test_inclusion_proof_reachable_
  unauthenticated` in `tests/test_gateway_facade_coverage.py`) all pass
  today only because `AURA_JWT_ENABLED` defaults to `False` in the test
  environment, so `JWTAuthMiddleware` is never installed in that test run
  at all — they never exercise the "auth armed" configuration production
  actually runs under. Same class of gap as BUG-001/BUG-002: a check that
  passes in an environment shape production doesn't use.
- **Caused by:** none
- **Fix:** #266 (`fix/uasr-public-verification-endpoints`) — added
  `/api/v1/counterfactual/jwks` and `/api/v1/counterfactual/audit/sth` to
  `_PUBLIC_PATHS`, and a new `_PUBLIC_PATH_PREFIXES` + `_is_public_path()`
  helper for the parameterized `/api/v1/counterfactual/audit/inclusion/{proof_hash}`
  route, applied to both `APIKeyMiddleware` and `JWTAuthMiddleware`. Sibling
  route `/api/v1/counterfactual/audit/financial` deliberately left requiring
  auth (negative test added). Re-verified live against
  https://dataaura.duckdns.org post-merge via `scripts/verify_live_deployment.py`.

## BUG-006: verify_live_deployment.py's webhooks check false-positived
- **Status:** false-positive
- **Found by:** live-verify 2026-08-31 (first run)
- **Severity:** cosmetic (test tooling, not product)
- **Root cause:** `scripts/verify_live_deployment.py`'s `check_webhooks`
  assumed the create-webhook response had `id` at the top level
  (`sub.get("id")`); the real, correct response nests it as
  `{"status": "success", "webhook": {"id": ..., ...}}`. The webhook was
  created successfully (confirmed by inspecting the raw response body in
  the failure detail) — the product worked, the check's own shape
  assumption was wrong.
- **Caused by:** none (script bug, not a regression from any prior fix)
- **Fix:** corrected inline in `scripts/verify_live_deployment.py`
  (same commit as this registry entry) — `sub.get("webhook", {}).get("id")`.
