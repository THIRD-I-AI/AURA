# AURA Strategy — one system for the data team

Last updated 2026-09-30. Shared, editable version (with diagrams and comments):
https://claude.ai/code/artifact/64100c88-da83-46d8-9676-bce2e3d32cc1 — this file is
the in-repo copy every Claude session reads. If they disagree, update both.

## Summary

AURA does the jobs of a **data engineer, data scientist and data analyst** in one
system, and signs its work so anyone can check it. The bet: as AI takes over data
work, the scarce thing is not answers but answers you can re-derive, verify and
defend. The sprint roadmap (S7–S30, P-1–P-3) and `docs/DATA_SUITE_ROADMAP.md` are
shipped; we are in a hardening phase (`docs/BUG_REGISTRY.md`).

## Why unifying the roles matters

Most data errors happen at handoffs between roles: a renamed column silently
breaks a dashboard, drift keeps a model reporting on data that changed meaning, a
correlation gets read as a cause. AURA puts all three roles on one shared
backbone — lineage, UASR drift watch, causal checks + DPC, and the signed
ledger — so the gap between roles is owned by the system.

## Honest gaps

| Area | Gap |
|---|---|
| Engineer | 4 DB connectors; no object storage (S3/Parquet/Iceberg) or Snowflake found; no user-declared data-quality tests; single node |
| Scientist | No predictive ML (train/evaluate/predict, experiment tracking) |
| Analyst | BUG-139, BUG-142, BUG-147 |
| Cross-cutting | Tenant isolation partial (BUG-062, BUG-072); fixes since 2026-09-27 not live-verified |
| Docs | README is stale: says DPC is off on chat and healing auto-deploys, but code defaults both safe (`agents/langgraph_orchestrator.py:206`, `uasr/service.py:117`); also says shims are lost on restart, but `hydrate_deployed_shims` reloads them |

## Roadmap

| Phase | Work | Gate to next phase |
|---|---|---|
| 1. Harden (now) | BUG-226..245, S55 truth pass, Dependabot triage, live verification | no `blocks-feature` bugs open; live verification green |
| 2. Fill the roles | S56, S57, S58, S60 | tenant isolation complete; ML and lakehouse merged |
| 3. Prove it | S59, pilot on a real dataset | one signed result through all three roles on real data |

Sprint rows are reserved in `docs/SPRINTS.md` → Backlog.

## Multi-agent execution plan

One lane = one Claude Code session = one branch = files it owns. Human review is
the bottleneck, so at most ~5 new lanes run at once.

| Lane | Work | Owns | Starts |
|---|---|---|---|
| A | Bug burn-down BUG-226..245 (existing session) | per bug, one PR each | running |
| B | S55 trust truth pass: confirm safe defaults live, correct README | `README.md`, `scripts/verify_live_deployment.py` | now |
| C | S56 tenant closure | `metadata_store/`, `alembic/` (sole migration owner) | after BUG-072 decision |
| D | S57 predictive ML v1 | new `ml_service/`, new gateway router `ml.py` | now |
| E | S58 lakehouse connector + data-quality tests | new files in `connectors/`, `connectors/registry.py` | now |
| F | S60 analyst UX (BUG-139/142/147/217–219) | `frontend/src/` only | now |
| G | Dependabot triage + live verification | `requirements*.txt`, `package.json`, `docs/LIVE_DEPLOYMENT_LOG.md` | now (background) |
| H | S59 cross-role end-to-end demo | `scripts/`, `tests_e2e/` | after D and E merge |

Coordination rules:

1. Branch `feature/S<id>-<slug>`; touch only your lane's files. Outside that, ask the owning lane.
2. Claim before coding: GitHub issue `Sprint <id>: <goal>` + In-flight row in `docs/SPRINTS.md` (append only).
3. Serialize hotspots: only lane C writes Alembic migrations; regenerate `openapi.json` + `sdk_clients/`
   right before merge, one PR at a time (SDK Codegen Sync); router registration in `api_gateway/main.py`
   stays a one-line add.
4. Merge `main` in, rerun the `.claude/rules/testing.md` pre-push gate, merge one PR at a time; never on red CI.
5. Bugs found by other lanes are filed in `docs/BUG_REGISTRY.md` for lane A, not fixed inside a feature PR.
6. A lane's "done" is not evidence — a human or the primary session reviews each diff before merge.

## Open decisions

- BUG-072: scope shared shims per tenant, or disable cross-source shim borrowing until scoped? (gates lane C)
- ~~Trust defaults~~ — decided 2026-09-30: on everywhere (already the code default); S55 verifies live.
- Predictive ML v1 scope: tabular train/evaluate/predict only (suggested), or also serving + tracking?
- Product shape: one self-serve workbench for all roles, or headless engine + internal review workbench?
