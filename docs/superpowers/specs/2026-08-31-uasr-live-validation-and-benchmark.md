# UASR Live Validation & Public Benchmark — Goal Tracker

> **Status: planned, not yet built.** This is a goals/results tracker, not a
> shipped feature — created in response to an explicit ask to (1) verify the
> AWS-staged deployment actually works and (2) prove UASR's self-healing on
> real data pipelines with results we can publish on GitHub. Update this
> file's Results sections as each workstream lands; don't let it go stale.

## Why this exists

Everything in `2026-08-30-uasr-effective-self-healing-gap-analysis.md` is
implemented and unit/integration tested (candidates #1–#5, PRs #256–#26x).
That proves the code does what it claims in a test harness. It does **not**
prove the deployed system works, and it does not give anyone outside this
repo a reason to believe the self-healing claim — a healthy CI badge isn't
evidence to a skeptical reader. This tracker closes that gap: real
deployment, real (or realistically-drifted) data, published numbers.

## Workstream 1 — Verify the live AWS staging deployment

**Goal:** confirm the staged instance is actually running the current code
and every UASR HTTP surface behaves as designed, not just that the
container started.

**Status: in progress.** Staging URL: `https://dataaura.duckdns.org`
(provided 2026-08-31; not previously recorded anywhere in the repo).

**Findings so far (unauthenticated checks only — no login credentials for
this instance yet):**
- `GET /health` → 200, `{"status":"healthy","environment":"production"}`.
  Gateway is live and running.
- `GET /uasr/deployment` at the bare path → returns the frontend SPA's
  `index.html`, not JSON. **Caddy only proxies `/api/*` and `/health`** to
  the gateway (`deploy/aws-free-tier/Caddyfile`); everything else falls
  through to the React app's history-API catch-all. The real path is
  `/api/v1/uasr/*`.
- `GET /api/v1/uasr/metrics` → 401 `AUTHENTICATION_REQUIRED`. Correct,
  expected behavior — the bearer-auth gate is armed on the live box.
- **Real gap found live, not in a test:** the gateway
  (`api_gateway/routers/pipelines.py`) only proxies **7** of UASR's HTTP
  endpoints — `ingest`, `baseline`, `metrics`, `drift/status`,
  `recovery/pending`, `approve`, `reject`. `/uasr/heal` (the endpoint that
  returns healed rows — the one recommended for external pipeline
  integration), `/uasr/deployment`, `/uasr/correlation`, and
  `/uasr/rollback` have **no gateway route at all**. An external caller
  hitting the public API today can get a monitor-only verdict via
  `/uasr/ingest` but cannot get healed data back through the gateway, and
  cannot check deployment config or correlation state remotely. This is a
  real product gap surfaced by actually testing the live deployment —
  exactly the kind of thing this workstream exists to catch.

**Full loop driven live (2026-08-31), with an authenticated test account:**
- `POST /uasr/baseline` for a new source (`live_verify_demo`) — 200,
  registered, `reference_version` returned.
- `POST /uasr/ingest` with a batch that renames `amount` → `total_amount`
  (a genuine schema-drift shape) — **drift correctly detected**: `schema`
  type, `high` severity, diagnosed exactly right
  (`"Added: ['total_amount'], Removed: ['amount']"` via `GET /uasr/drift/status`).
- **First attempt (2 rows) did NOT auto-heal**: `status: "failed"`,
  `shim_deployed: false`. Initially suspected a missing LLM key — **wrong,
  ruled out below** — the reflector/actuator path for a clean 1:1 rename
  is fully rule-based/template (`reflector_agent.py::_diagnose_schema_drift`
  assigns confidence 0.75 for "added and removed, likely a rename," above
  the 0.6 LLM-fallback threshold; `actuator_agent.py::_schema_shim` has a
  dedicated `_RENAME_MAP` template). Confirmed by latency: every attempt
  below completed in 2–6 **milliseconds** — no LLM round trip ever happened.
- **Root cause, verified live with an isolated retest:** a second attempt
  (30 rows, but with a *different* `user_id` range than the baseline — an
  unintentional confound in the test data, not the product) also failed,
  with a real nonzero `post_kl`. A third attempt — same 30 rows, same
  values as the baseline, **only** the column renamed — **succeeded**:
  `status: "deployed"`, `shim_deployed: true`, `post_kl: 0.0`. Self-healing
  works correctly for a clean schema rename.
  
  The two "failures" exposed a genuine, separate bug rather than being
  pure test artifacts: `RecoveryLoop._validate_shim`
  (`aurabackend/uasr/recovery_loop.py:422`, pre-fix) conflates "did this shim fix
  the drift it was generated for" with "is the batch drift-free in every
  dimension." `drift_detector.detect()` checks schema drift first and
  returns early (`drift_detector.py:214-226`); once schema is clean it
  separately checks statistical drift on the same batch
  (`drift_detector.py:228-241`). If a real-world schema migration batch
  *also* carries incidental statistical variation — a completely normal
  co-occurrence — `post_drift.drift_type` comes back `STATISTICAL`, and
  the check (`drift.drift_type == SCHEMA and not
  post_drift.drift_detected`) can never pass, because it requires **zero**
  drift of any kind rather than checking specifically that the schema
  problem is resolved. That line is **provably dead code today** — it's
  a strict subset of the `not post_drift.drift_detected` check on line 339,
  so it can never evaluate true when line 339 didn't already return.
  A perfectly correct schema shim is silently rejected whenever the
  corrected batch happens to carry unrelated statistical noise.
- **Separate bug, also confirmed live**: `GET /uasr/metrics` recorded the
  first (failed) SCHEMA drift event under `by_drift_type:
  {"statistical": ...}` — a real mis-attribution.
  `metrics.record_from_loop_result()` defaults `drift_type` to
  `STATISTICAL` when `loop_result.diagnosis` doesn't carry a recognized
  `drift_type` attribute; on a FAILED recovery the diagnosis object's shape
  isn't what the metrics code expects, so it silently falls to the wrong
  bucket. Independent of the validator bug above, but found via the same
  investigation.

**Net assessment:** detection AND end-to-end self-healing are confirmed
genuinely working live for the common case. The two real bugs found
(the schema-validation false-reject, and the metrics drift-type
mis-attribution on a failed recovery) are being fixed on
`fix/uasr-schema-validation-false-reject`. The missing `/uasr/recovery/{id}`
gateway route (and the rest of the proxy gap below) is fixed on
`fix/uasr-gateway-proxy-gaps`, so this class of failure will be diagnosable
without box access once that merges.

**What "verified" means** (once the URL is available):
1. `GET https://<host>/health` (gateway) — 200, confirms the box is up.
2. `GET https://<host>/uasr/deployment` (proxied) — confirms the running
   image's actual config: which flags are on, `state_backend`/
   `repair_backend`, and — this is the real check — whether it's running
   the version with candidates #1–#5 (`repair_max_per_source`,
   `correlation_window_seconds` etc. present in the response means yes;
   absent means the box is on an older image and needs a redeploy first).
3. `POST /uasr/baseline` then `POST /uasr/heal` with a small synthetic
   batch that has an obvious, deliberately-triggered drift (e.g. a renamed
   column) — confirms the full detect → diagnose → generate → validate →
   deploy → return-healed-rows loop actually executes against the live
   box, not just in a test's mocked agents.
4. `GET /uasr/metrics` — confirms the Hᵤ tracker recorded that attempt.

**Deliverable:** a `scripts/verify_staging.sh` (or `.py`) that runs steps
1–4 against a `$STAGING_URL` env var and prints pass/fail per step —
reusable after every future deploy, not a one-off manual check.

## Workstream 2 — Public-dataset, realistically-drifted benchmark

**Goal:** attach UASR to a data pipeline built from a real, well-known
public dataset — not synthetic random numbers — replay a drift event that
genuinely happened (or is realistic to that domain), and publish the
measured before/after numbers. Fully reproducible by anyone who clones the
repo and runs one script; no proprietary data, no staging access required.

**Status: Scenario 1, the congestion-surcharge retry follow-up, the
deliberate-rollback demo, and Scenario 2 (cross-source correlation) are all
built and run (2026-09-01), results published below and in
`docs/UASR_BENCHMARK_RESULTS.md`.**

### Dataset choice: NYC TLC Trip Record Data

[NYC Taxi & Limousine Commission trip records](https://www1.nyc.gov/site/tlc/about/tlc-trip-record-data.page)
— public, monthly Parquet files, no auth, no rate limit that matters at
demo scale, downloaded fresh at runtime (TLC's redistribution terms could
not be confirmed — nyc.gov's Terms of Use page 403s automated fetches —
so nothing is committed to the repo; `scripts/uasr_benchmark_nyc_taxi.py`
downloads and caches to a gitignored `.cache/` directory).

**Revised from the original plan:** a TLC-documented column *rename* with
a citable date could not be found (TLC's data dictionary is a rolling
current-state document, not a versioned changelog). What research did
confirm, with a citation, is a real *value-population* event: New York's
congestion surcharge on Manhattan-bound trips was enacted 2019-01-01 but
blocked by a court TRO; collection genuinely began 2019-02-02
(https://www.nyc.gov/site/tlc/about/congestion-surcharge.page). TLC's
January 2019 file carries the `congestion_surcharge` column 100% null;
February 2019 has it populated — confirmed both by this benchmark's own
run (see Results) and independently at
https://github.com/KyleHaynes/NYC-2019-01-Yellow-Taxi-Data.

### Scenario 1 — single-source drift (built, run 2026-08-31)

1. Ingest sample batches of the real January 2019 file for one source_id
   — establishes the baseline (`scripts/uasr_benchmark_nyc_taxi.py`).
2. Replay the real February 2019 batch. **Finding, not assumption:**
   `DriftDetector._compute_distributions` drops an all-null column from
   the baseline entirely (`aurabackend/uasr/drift_detector.py:568-570`) —
   correct null-handling, not a bug, but it means `congestion_surcharge`
   itself can never be baseline-registered, so the real event's only
   detectable surface is its downstream effect on `total_amount`. At this
   benchmark's sample size (2000 rows/month) that shift did **not** cross
   the adaptive KL threshold — reported honestly as a negative result
   rather than forced. See Results below and Follow-ups.
3. Separately, inject a realistic *numeric* drift on a clean month — a
   fare-amount unit error (cents vs. dollars, applied to the exact rows
   used as baseline, i.e. "this batch got reprocessed upstream with a
   scale bug"). This is synthetic and labeled as such in the script's
   docstring, unlike step 2. **Result: full end-to-end self-heal,
   auto-deployed, zero residual KL divergence, no LLM call needed** (the
   rule-based reflector's statistical-drift confidence, 0.7-0.85, always
   clears the 0.6 LLM-fallback threshold). See Results below.
4. `UASR_POST_HEAL_VALIDATION_BATCHES` auto-rollback demo — built as a
   follow-up (see below): deploys a rescale shim for an injected x100
   fare-unit bug, then replays batches carrying a *different*, uncorrected
   x1000 scale error the deployed shim does not fix. **Result: the shim
   auto-reverted after 2 post-heal batches still showed the same drift
   type**, exactly as designed — proving the mechanism actually reverts a
   bad heal instead of leaving it silently permanent. See Results below.

### What gets measured and published

For each scenario: batches processed, drift detected (type, severity),
time-to-diagnose, time-to-deploy, validation pass/fail, Hᵤ score
before/after, and for Scenario 2 specifically — the correlation event's
`source_ids`/`window_seconds` and whether auto-heal actually saved
redundant diagnose+generate work (compare wall-clock/LLM-call count with
`UASR_CORRELATION_AUTO_HEAL` on vs. off).

**Deliverables:**
- `scripts/uasr_benchmark_nyc_taxi.py` — built. Downloads the two public
  monthly files at runtime (cached, gitignored, never committed — see
  license note below), drives `DriftDetector`/`RecoveryLoop`/
  `HealingMetricTracker` directly in-process (no LLM call needed for
  either scenario, no docker/Kafka/Redis required — a deliberate choice
  over the originally-planned `docker compose up uasr_service` HTTP path,
  since the in-process modules are what the scenarios actually exercise
  and it keeps `python scripts/uasr_benchmark_nyc_taxi.py` a true
  one-command reproduction), and writes both a results JSON and this
  Markdown report.
- `docs/UASR_BENCHMARK_RESULTS.md` — built, regenerated on each run.
- README "Proven on real data" section — done, links to the results doc.

## Resolved: dataset research (2026-08-31)

- **Schema-change date:** no citable TLC-documented column *rename* found.
  Used instead: the real, dated `congestion_surcharge` null→populated
  transition described above (Jan/Feb 2019).
- **Redistribution:** TLC's trip data is governed by NYC's general Terms
  of Use (via the AWS Open Data registry listing), not a permissive
  open-data license, and the terms page itself blocks automated fetches —
  so the script downloads fresh from TLC's CDN at runtime rather than
  committing any sample rows to the repo.

## Follow-ups

All three follow-ups below (retry at scale, rollback demo, Scenario 2) are
now built and run — `scripts/uasr_benchmark_nyc_taxi.py`,
2026-09-01. What's left is genuinely open, not silently dropped:

- The retry (larger sample, Manhattan-only) still did not make the real
  congestion-surcharge event detectable on `total_amount` — see Results
  below. Whether a larger fleet (more months, more rows) would finally
  cross the threshold is untested; this benchmark's scale ceiling is
  ~20,000 raw rows/month for a tractable runtime.
- **A real bug was found and fixed while running the retry at n=20,000
  rows/month**, not part of the original plan: `DriftDetector` overflowed
  and permanently stopped detecting for a source when a baseline column is
  effectively constant (float-rounding noise instead of an exact 0.0 std).
  See `docs/BUG_REGISTRY.md` BUG-012 and Results below.
- The cross-source correlation Part A (the real event, replayed per
  borough) did trip `detect_correlation()`, but investigation showed the
  three boroughs' affected columns don't share a cause traceable to the
  surcharge — see the "Honest read" note in
  `docs/UASR_BENCHMARK_RESULTS.md`'s Scenario 2 section. Part B (synthetic,
  identical bug across boroughs) is what actually proves the
  detect-correlation + shim-borrowing mechanism end to end with a known
  common cause.
- `UASR_CORRELATION_AUTO_HEAL` (the env-var-gated auto-heal path, as
  opposed to calling `run_with_candidate_shim` directly the way the
  benchmark script does) was not exercised — the script drives
  `find_recent_deployed_shim` + `run_with_candidate_shim` directly
  in-process rather than through the env-var-gated service wiring, matching
  this benchmark's existing no-Docker/no-service-process design. The
  service-level wiring is already covered by
  `aurabackend/tests/test_uasr_service_cross_source_heal.py`.

### Scenario 2 — cross-source correlation (candidate #5, built)

Split trip records by borough as **separate source_ids**
(`nyc_taxi_manhattan`, `nyc_taxi_queens`, `nyc_taxi_brooklyn` — Bronx and
Staten Island excluded, yellow-cab volume there is too sparse for a stable
baseline at a tractable sample size) and replayed two variants: Part A, the
real congestion-surcharge event per borough; Part B, the same synthetic
x100 fare-unit bug injected identically into all three. Part B trips
`detect_correlation()` and demonstrates the cross-source shim-borrowing
path (`find_recent_deployed_shim` + `RecoveryLoop.run_with_candidate_shim`)
end to end: Manhattan diagnoses+generates+deploys normally, Queens and
Brooklyn each borrow and validate a sibling's shim (skipping diagnose+
generate) rather than independently re-diagnosing the same drift. See
Results below for numbers.

## Results (fill in as each workstream lands)

- **Workstream 1 (2026-08-31):** live box confirmed up, running production
  code, auth gate correctly armed. Drove the real loop with a test account
  three times: detection is confirmed genuinely working live; an isolated
  retest (identical values, rename only) confirmed **end-to-end self-heal
  works live** (`deployed`, `post_kl: 0.0`). Two real bugs found and root-
  caused along the way, both fixed on `fix/uasr-schema-validation-false-
  reject`: (1) `RecoveryLoop._validate_shim` rejects a correct schema-fix
  shim whenever the corrected batch also carries unrelated statistical
  drift — a real-world-common co-occurrence, and the offending line was
  provably dead code; (2) `metrics.record_from_loop_result()` mis-attributes
  a failed schema-drift event's `drift_type` to `statistical`. Also fixed:
  the gateway only proxied 7 of ~20 UASR endpoints — `/uasr/heal` itself
  had no route — closed on `fix/uasr-gateway-proxy-gaps` with a regression
  test that diffs UASR's route set against the gateway's.
- **Workstream 2 (2026-08-31, extended 2026-09-01):** Scenario 1 built and
  run against real TLC data (`scripts/uasr_benchmark_nyc_taxi.py`, full
  numbers in `docs/UASR_BENCHMARK_RESULTS.md`). The real, dated event
  (congestion surcharge, Jan/Feb 2019) confirmed its own premise —
  `congestion_surcharge` null rate measured 100% in January, 0% in
  February, exactly matching the cited 2019-02-02 collection start — but
  did not cross the drift threshold on `total_amount` at n=2000 rows/month,
  reported as a negative result rather than forced. The synthetic
  fare-unit-bug injection produced a full, honest end-to-end self-heal:
  `statistical`/`critical` drift detected, template-generated rescale shim
  auto-deployed (no LLM call, no human review — S41's template tier),
  post-heal KL divergence `0.0`, Hᵤ = 4.615.

  **Follow-ups (2026-09-01):**
  - **Congestion-surcharge retry at n=20,000 rows/month, all boroughs and
    Manhattan-only:** `drift_detected` flips to `True` in both — but
    `total_amount` itself is not among the affected columns in either run,
    and its own KL (1.77 all-boroughs, 0.73 Manhattan-only) stays well
    below each run's adaptive threshold zeta (6.86, 7.08 respectively).
    What actually crosses threshold is `VendorID` (a mundane which-
    vendor-recorded-more-trips mix shift between the sampled Jan and Feb
    slices) and, in the all-boroughs run, `mta_tax` (a normally-constant
    $0.50 column with a single anomalous $0.00 row interacting with the KL
    calculator's known sensitivity to near-constant columns). **Remains a
    negative result for the real event** — larger scale and Manhattan-only
    filtering surfaced unrelated month-to-month noise, not the surcharge's
    effect on `total_amount`.
  - **Real bug found and fixed while running the retry (BUG-012,
    `docs/BUG_REGISTRY.md`):** `aurabackend/uasr/drift_detector.py`'s
    near-zero-std guard (`ref_dist.std > 0`) only caught an exact `0.0`
    std, not the float-rounding noise (`~5.55e-17`) `numpy.std` returns for
    a genuinely-constant real column (TLC's `improvement_surcharge`, always
    $0.30). That noise floor became the denominator of a location-shift
    score, turning any later batch's float-precision mean residual into an
    astronomically large fake "sigma" count — which then poisoned the
    persisted, unbounded `kl_history` the adaptive threshold reads back on
    every subsequent batch, compounding roughly geometrically until KL
    overflowed a Python float (`OverflowError`), permanently breaking
    detection for that source. Fixed by flooring the guard at the module's
    own existing `_EPS = 1e-10` instead of `0`; re-verified against the
    same 20,000-row replay that originally crashed (now completes cleanly,
    max KL 22.88) plus two new regression tests in
    `aurabackend/tests/test_uasr_drift_detector.py::TestNearConstantColumnFeedbackOverflow`.
  - **Deliberate-rollback demo
    (`UASR_POST_HEAL_VALIDATION_BATCHES=2`):** deployed a rescale shim for
    an injected x100 fare-unit bug (`deployed`, `post_kl: 0.0`), then
    replayed 2 batches carrying a *different*, uncorrected x1000 scale
    error the deployed shim does not fix. Both post-heal batches still
    showed `statistical` drift; on the 2nd, `RecoveryLoop.check_post_deploy`
    correctly auto-reverted the shim (`rollback_last_shim`) — deployed-shim
    count for the source went from 1 to 0. Confirms the auto-rollback path
    genuinely reverts a bad heal rather than leaving it silently permanent.
  - **Scenario 2, cross-source correlation (candidate #5), built and run:**
    Part A (real event, per borough) tripped `detect_correlation()` (3
    boroughs within a 300s window), but each borough's affected columns
    differ and are largely unrelated to `total_amount` — consistent with
    independent per-borough sampling noise coinciding in time, not a shared
    cause; `CorrelatedIncident` is deliberately report-only, not causal
    inference, and this result is exactly the caveat its own docstring
    makes. Part B (synthetic, identical x100 bug across Manhattan/Queens/
    Brooklyn) is what proves the mechanism end to end: Manhattan
    diagnoses+generates+deploys normally (`full_loop`), Queens and Brooklyn
    each borrow and validate a sibling's shim via
    `find_recent_deployed_shim` + `RecoveryLoop.run_with_candidate_shim`
    (`cross_source_borrowed`, skipping diagnose+generate) rather than
    independently re-diagnosing the same drift — all three deploy
    successfully, and `detect_correlation()` fires. Latency savings from
    skipping diagnose+generate are not dramatic here (~10-13ms either way)
    because none of this benchmark's scenarios need an LLM call (the
    rule-based reflector always clears the 0.6 LLM-fallback confidence
    threshold) — the larger savings this mechanism is designed for (an
    LLM round-trip avoided per sibling) is not exercised by this benchmark
    by construction.
- **Workstream 1, full-suite re-verification (2026-09-01):** ran the
  general-purpose `scripts/verify_live_deployment.py` (all 12 promised-
  feature checks, not just UASR) against the live box for the first time
  since it was written. No existing STAGING_EMAIL/STAGING_PASSWORD test
  account could be found anywhere in the repo or shell env, so a new
  dedicated verify-only bot account was registered directly via
  `POST /api/v1/auth/register` (id `5351fe51-0320-4dd7-bd88-98ed9fe5f507`,
  name "AURA Live-Verify Bot") — leave it in place for future runs rather
  than re-provisioning each time. Result: **12/12 passed, 0 failed, 0
  skipped** — health, login, jwks, financial_audit_demo, query_history,
  saved_queries, dashboards_list, dashboard_create, chat, pipeline_generate,
  webhooks, uasr_self_heal all green. Full report:
  `live_verify_20260901_203313.md` (repo root, gitignored scratch output).
  No new bugs found; every promised feature on this deployment works live
  as of this run.
