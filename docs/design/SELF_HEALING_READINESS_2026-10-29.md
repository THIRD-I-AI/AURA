# Self-healing readiness, 2026-10-29

Evidence-based audit of whether AURA's data pipelines heal themselves, in code and in the
production configuration (workflow `wf_37b49411-ac0`: five investigators, five adversarial
challengers, one synthesis that re-read every disputed claim). Three claims were re-checked by
hand: the dead `uasr.drift` webhook, MonitorAgent's unauthenticated UASR call, and the connector
bridge re-baselining on every request. All three hold. The plan that follows from it is ADR-002.

**Re-verification.** I re-read the code for the points the five challenges disputed:

- **The `uasr.drift` webhook is dead.** The poller publishes only the public metric keys (`api_gateway/main.py:94-97,119-127`). `drift_detected` is not among them, and `webhook_dispatcher.py:133-136` fires `uasr.drift` only when that key is present. No other code publishes on a `uasr:*` topic. The detect challenge is right and the wiring-prod challenge is wrong.
- **`deploy/aws-free-tier/.env.example` exists.** It is a dotfile, so a plain `ls` hides it. The verify-rollback challenge was wrong to say it does not exist. It sets `AURA_JWT_ENABLED=true` at line 45 and no `UASR_*` or `KAFKA` variables. The live `.env` cannot be checked from the repo.
- **The severity cutoff for auto-deploy is 2ζ, not 3ζ.** Above 2ζ is HIGH and above 3ζ is CRITICAL (`drift_detector.py:654-659`). `_should_deploy` only lets low and medium through (`recovery_loop.py:645-656`).
- **MonitorAgent's call to UASR fails in production.** It is reachable through `inbound_hooks._fire_agent` (`inbound_hooks.py:302-312`; registered at `agents/executor.py:29,52`). But its POST to `/uasr/ingest` carries no Authorization header (`monitor_agent.py:186-193`). UASR runs JWT middleware when JWT is enabled (`service.py:446` → `shared/service_factory.py:134-136`), and no `/uasr` path is public (`shared/middleware.py:141-192`). The 401 is ignored silently (`monitor_agent.py:197`).
- **Nothing calls `/uasr/heal`.** Outside tests, the only reference is the gateway proxy (`api_gateway/routers/pipelines.py:554-565`).
- **`check_post_deploy` has one caller,** `mapek_worker.py:564`.

---

### 1. Verdict

**No.** In production (`deploy/aws-free-tier/docker-compose.yml` plus the committed `.env.example`), AURA does not autonomously heal any of its own pipelines.

The UASR service is deployed and its HTTP recovery loop works. But it only runs when something calls `/uasr/ingest` or `/uasr/heal`, and AURA's own pipelines never do:
- `/pipeline/execute`, ETL, streaming, uploads, saved queries and sync-to-chat never call UASR (`routers/pipelines.py:143-160`).
- The connector bridge calls `/uasr/ingest`, which only returns a verdict. It never changes rows and never applies fixes already deployed (`service.py:590-685`). The bridge also re-registers the baseline on every request (`connections.py:545,672-686`).
- `/uasr/heal`, the only endpoint that returns repaired rows, has no caller.

The always-on MAPE-K worker (the continuous monitor-and-repair loop) is switched on (`compose:154`), but it is dead:
- `UASR_KAFKA_SERVERS` is never set, so it defaults to `localhost:9092` (`mapek_worker.py:111`).
- The free-tier compose has no broker.
- No code produces to `aura.uasr.events`.
- The bootstrap retries forever (`service.py:180-221`).

Even when the loop does run, risk-tiering (on by default, `service.py:125`) lets only template fixes at LOW or MEDIUM severity deploy on their own. In practice that is two fixes:
- **Dropping newly added columns.**
- **Casting a retyped column back.** Values that fail the cast become None.

Everything else is held for a human. Held items time out after 1800 s and are escalated, never deployed (`service.py:225-270`).

After a fix deploys, nothing checks it or rolls it back:
- Post-heal validation defaults to 0 (`runtime_config.py:99-107`) and runs only on the dead Kafka path.
- On `/uasr/heal`, a fix that does not work is not reverted. A further fix is stacked on top.
- Drift baselines are held in memory, so a restart wipes them.
- Drift raises no alert to anyone.

So today this is on-demand drift diagnosis plus a narrow set of auto-applied fixes that no pipeline consumes. It is not self-healing pipelines.

### 2. Matrix

Cells read yes / gated-off / heuristic / no.

| Failure class | Detected? | Repaired autonomously? | Verified / rolled back? | Live in prod? |
|---|---|---|---|---|
| New upstream column | yes, on push only (`drift_detector.py:402-475`). Connector path re-baselines every request (`connections.py:672-686`) | heuristic: drops the column, MEDIUM so it auto-deploys (`actuator_agent.py:214-229`; `recovery_loop.py:655`) | sandbox check on the triggering batch only (`recovery_loop.py:450-560`). Post-deploy check gated-off (`runtime_config.py:99-107`) | no: only via `/uasr/heal`, which has no caller (`pipelines.py:554`) |
| Column retype | yes, only when the caller sends `schema_snapshot` (`drift_detector.py:425-444`) | heuristic: casts back, failures become None (`actuator_agent.py:157-190,171`) | as above | no, same reason |
| Column removed or renamed | yes, but a rename shows as added + removed (`drift_detector.py:452-475`) | no: HIGH/CRITICAL is held (`drift_detector.py:464-466`; `recovery_loop.py:655`). Rename matching is `zip(added, removed)` (`actuator_agent.py:233`) | manual approve/rollback only (`service.py:1111-1144,1255-1272`) | no |
| Unit or scale error | yes, KL; bins are not aligned to the baseline, so false positives (`drift_detector.py:889-911,780-805`) | heuristic: only if a hardcoded factor matches and KL ≤ 2ζ (`actuator_agent.py:279`; `drift_detector.py:654-659`). NumericHeal gated-off and Kafka-only (`runtime_config.py:64-79`) | gated-off | no |
| Outlier or location shift | yes | no: clip only fires above 5ζ, which is always CRITICAL, so it is held. Location shifts escalate (`actuator_agent.py:81-85,307-347`). Below 2ζ the shim is a pass-through and fails validation (`:359-375`) | n/a | no |
| Semantic drift | yes, only if schema and stat checks did not fire (`drift_detector.py:351-389,701-740`) | no: LLM shim is always held (`recovery_loop.py:653`) | n/a | no |
| Null spike | no: computed but never compared (`drift_detector.py:873,881`); all-null columns skipped (`:852-854`) | no | no | no |
| Volume drop or spike | no: only absolute empty or over-cap checks (`pipeline/engine.py:430-434`) | no | no | no |
| Duplicate rows | no | no | no | no |
| Late or missing run, freshness | no; the scheduler only catches up (`scheduler_service/repository.py:129-137`) and is not deployed | no | no | no |
| Pipeline run failure | yes: run marked FAILED (`pipeline/engine.py:314-318`); `pipeline.failed` webhook if the user subscribed (`webhook_dispatcher.py:121-131`) | no: no retry on the engine path | no | detect/alert only |
| Scheduled job failure | gated-off: scheduler_service not in free-tier compose | heuristic: fixed-delay re-run then notify (`executor.py:196-276`) | no | no |
| Source unreachable or auth failure | heuristic: generic False / 502 (`postgresql_connector.py:38-54`; `connections.py:609-611`). `health_check` never fails (`base.py:135-141`) | no | no | no |
| Streaming source or sink failure | no: swallowed, status stays RUNNING (`streaming_engine.py:502-503,543-548`; `kafka_source.py:100-114`) | heuristic: websocket reconnect, webhook-sink backoff (`websocket_source.py:131-164`; `webhook_sink.py:109-124`) | no | partial transport retry only |
| Bad fix makes things worse | no: watch only checks whether the same drift type still fires (`recovery_loop.py:894-946`) | no | gated-off: auto-rollback only at `mapek_worker.py:564`. Canary promote/revert has no caller (`shim_router.py:294-449`) | no; manual rollback only |
| Drift alerting | n/a | n/a | n/a | no: `uasr.drift` webhook is dead (`main.py:94-97` vs `webhook_dispatcher.py:133-136`) |

### 3. Ranked gaps

1. **No AURA pipeline routes data through `/uasr/heal` before writing it** (`pipeline/engine.py`, ETL, streaming, connector bridge, sync-to-chat). **[L]**
   - Add a heal step after source load and per micro-batch.
   - Write the returned rows to the destination.
   - Quarantine or fail the run when `healed=false`.
2. **No continuous trigger.** The Kafka loop is dead and nothing schedules ingest. **[M]** Pick one:
   - Set `UASR_KAFKA_SERVERS` and add a broker plus a producer.
   - Better for the 1 GB box: give MAPE-K a non-Kafka input, such as an in-process queue or a scheduled poll like the saved-query loop (`queries.py:1034`).
   - Add a startup check that fails loudly when MAPE-K is enabled with no broker.
3. **Baselines are thrown away.** The connector bridge re-baselines every request, and the state store is in memory (`runtime_config.py:179-184`; `compose:175`). **[S–M]**
   - Baseline only when none exists, or on operator approval.
   - Use a durable SQLite/DuckDB state store on `/data`.
4. **No verification or rollback on the HTTP path, and fixes stack.** **[M]**
   - Call `check_post_deploy` and `mark_shim_rolled_back` from `heal_batch`.
   - Set `UASR_POST_HEAL_VALIDATION_BATCHES>0` in compose.
   - Measure the baseline distance over N batches, not one clean batch.
   - Save the watch state.
5. **The autonomy policy holds every real repair** (removals, renames, scale > 2ζ, LLM shims), and the reaper only escalates. **[M + user policy decision]**
   - Add a verified auto-commit tier, for example a K-of-K canary with auto-revert, for HIGH-severity template fixes, especially exact renames.
   - This is only safe after gap 4 is closed.
6. **The fixes are lossy guesses.** **[M]**
   - Match renames by name or value similarity instead of list order.
   - Quarantine values that fail a cast instead of nulling them.
   - Offer schema evolution instead of dropping new columns.
   - Implement the winsorize template.
   - Make retries change strategy, for example escalate to the LLM or another template.
7. **Missing detectors and a broken one.** **[M]**
   - Add null-rate, volume, duplicate and freshness checks.
   - Fix bin alignment (bin each batch on the baseline's edges).
   - Report all drift types, not just the first.
   - Add typed connector errors and a `health_check` that can fail.
   - Report streaming source and sink errors as DEGRADED.
8. **No alerting or audit trail.** **[S–M]**
   - Publish per-source drift events so the `uasr.drift` webhook actually fires.
   - Write an append-only, signed log of every autonomous action through `shared/audit_ledger`.
   - Send the auth header on MonitorAgent's UASR call.
9. **Kafka-only advanced healers** (numeric heal, ShimRouter, martingale) are flag-off and also have only a dead consumer. **[M]** Port them to the live path, then enable and verify them.
10. **Scheduler is not deployed**, and its retries are fixed-delay with no error classification. **[M]** Embed or deploy it, add backoff and transient-vs-permanent classification, and connect failures to UASR.

### 4. An honest end-to-end production proof

Run against the deployed box, with the real compose and `.env`, and no approve calls, mocks or patched components:

1. **Setup.** Upload CSV v1, save a real pipeline (source → transform → sink), and run it on its schedule, not by hand. The baseline should be registered automatically.
2. **Fault.** Upload v2 with one column renamed, one retyped and one value ×100.
3. **Healed run.** The next scheduled run must write output that matches an expected-output fixture value for value. The run record must show the drift, the recovery_id and `healed=true`, all with no human action.
4. **Standing fix.** A third run applies the fix that is already deployed and creates no new drift event or recovery.
5. **Bad-fix test.** Inject a fault where the fix makes things worse. The post-heal check must roll it back within N batches, show ROLLED_BACK, and raise an alert to a subscribed webhook.
6. **Restart.** Restart `uasr_service`. Baselines, deployed fixes and watch state must survive. Step 4 must still pass afterwards.
7. **Continuous loop.** Report `/uasr/mapek/status` as running and add it to `scripts/verify_live_deployment.py`. This replaces `uasr_self_heal` (`:347-395`), which today uses the verdict-only `/uasr/ingest`, approves its own fix, and never checks rows.
8. **Coverage.** Repeat for null-spike, volume-drop, duplicate and missing-run faults. Each must be detected and alerted, and repaired or quarantined.

None of these steps passes today.