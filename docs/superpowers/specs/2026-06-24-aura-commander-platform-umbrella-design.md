# AURA Commander Platform — Umbrella Architecture

**Status:** Umbrella design (program-level). Each subsystem below gets its own
detailed spec → plan → build cycle. This document defines the subsystems, their
boundaries, and the interfaces between them so they are not designed in conflict.

**Date:** 2026-06-24
**Author:** Mounith + Claude (Opus 4.8)

---

## Vision

Turn AURA's chat from a rigid reactive pipeline into an **intelligent application
that works continuously on its own, keeps results ready, and delivers on request
with near-zero latency** — runnable both as a hosted cloud SaaS and as an
air-gapped on-prem install, on the user's own terms with no vendor lock-in.

Three shifts define it:

1. **Orchestration-as-reasoning, not orchestration-as-code.** One provider-agnostic
   agentic tool-loop replaces the hardcoded `IntentAgent` + `run_orchestrator` DAG.
   The model decides which capabilities (tools) to use; we own the tools and the
   guardrails.
2. **Proactive, not reactive.** A standing-work engine prepares answers before they
   are asked — refreshing data representations, running standing analyses, and
   pre-scanning for anomalies — so the chat becomes a *delivery surface*, not a
   compute trigger.
3. **One click to everything.** Whatever the system observes is one click from its
   full context. No nesting.

### Non-goals (this program does NOT)

- Rebuild the DuckDB engine, the pipeline engine, the forensic auditor logic, file
  storage, Ed25519 signing, tenant isolation, or the React app shell. These are the
  working muscles; we re-wire orchestration around them.
- Lock the product to any single model vendor.
- Ship an ungoverned autonomous agent (see Governance).

---

## The shared layer (the spine that connects everything)

The reactive commander (A) and the proactive engine (E) are two faces of one system.
They meet at a **shared preparation layer**:

- **Data Cards (B):** compact, AI-native per-dataset representations.
- **Results Cache:** content-addressed, tenant-scoped store of pre-computed analysis
  results + a ranked "attention queue."

A *reads* what E *prepared*. This single seam is what makes "deliver as per request"
feel instant, and it lets A and E be built and tested independently.

```
                 ┌─────────────────────────────────────────┐
   uploads /     │   E. Standing-Work Engine (proactive)    │
   schedules ───▶│   cards refresh · standing analyses ·    │
                 │   anomaly/drift/forensic pre-scan        │
                 └───────────────┬─────────────────────────┘
                                 │ writes
                 ┌───────────────▼─────────────────────────┐
                 │   SHARED LAYER                           │
                 │   B. Data Cards   +   Results Cache      │
                 └───────────────┬─────────────────────────┘
                                 │ reads
   user asks ───▶┌───────────────▼─────────────────────────┐
                 │   A. Commander Core (reactive tool-loop) │──▶ streams to F
                 │   guarded tools · run_audit → C          │
                 └──────────────────────────────────────────┘
   all of the above runs under the active deployment profile (D: cloud | on-prem)
   and is surfaced one-click in the cockpit (F)
```

---

## Subsystems

### A. Commander Core (the spine)

**Purpose:** a provider-agnostic agentic tool-loop that replaces `IntentAgent` +
`run_orchestrator` in `api_gateway/routers/chat.py`.

**What it does:** takes the user message + a system prompt (carrying data-card
context from B and ready results from the cache) + a tool registry; calls the model;
executes tool calls; streams events back; loops until done.

**New seams:**
- `chat_with_tools()` added to `shared/llm_provider.py` — maps a single tool-calling
  interface onto each backend's native function-calling (OpenAI/Groq/Gemini direct;
  Ollama for tool-capable local models) with a **ReAct-style JSON fallback** for
  weaker local models, so the air-gapped path never depends on a vendor feature.
- A **tool registry**: thin adapters over existing functions — `run_sql`,
  `create_pipeline`, `attach_pipeline`, `run_audit`, `list_files`, `create_dashboard`,
  `get_data_card`, `get_ready_result`. The terminal's code-gen calls the same
  registry → one brain, not two.

**Guardrails at the tool boundary (deterministic, model-independent):** `run_sql`
goes through `SQLSafetyValidator` + `quote_identifier`/`quote_literal` + SELECT-only;
every tool receives the request's tenant from the verified context and never trusts
a tenant supplied by the model; tool availability is constrained per context.

**Streaming:** the loop yields typed events (`text_delta`, `tool_call_start`,
`tool_result`, `done`) over Server-Sent Events.

**Consumes:** Data Cards + Results Cache (shared layer). **Calls:** the ledger (C)
via `run_audit` and other mutating tools. **Obeys:** the active deployment profile
(D) for model selection.

### B. AI-native Data Cards

**Purpose:** a representation of each dataset optimized for fast, accurate LLM
reasoning — read *instead of* raw schema dumps.

**Contents (per dataset, per fingerprint):** semantic column types, value
distributions / cardinality, key & foreign-key relationships, representative sample
values, and a short natural-language summary. Cacheable as a stable prompt prefix.

**Built on:** the existing `schema_indexer.py` + `data_utils.py`. **Stored in:** the
durable tenant-scoped metadata store, keyed by `(tenant, dataset, fingerprint)`,
rebuilt only on file change.

**Research track:** before committing to a card format, do a literature pass
(table-serialization-for-LLMs, NL2SQL schema linking, semantic data profiling) and
report whether the format is genuinely novel and worth publishing. This is the one
subsystem with an explicit research gate.

**Produced by:** an async pass on upload (triggered by E). **Consumed by:** A.

### C. Durable Audit Ledger

**Purpose:** make the audit *history* tamper-evident in practice, not just in code.

**The validated gap (as of 2026-06-23):** the hash-chain WORM log
(`shared/audit_log.py`) exists with `prev_hash → record_hash`, daily Merkle roots,
and inclusion proofs — but it is **off by default** (`AURA_AUDIT_ENABLED` defaults
to `false`, every append early-returns), and even when on it is a **single-process
writer to a local file** (`/var/log/aura/audit`). So `sign_and_persist` *calls*
`audit_event("financial_audit_completed", …)` but on a default deployment that call
does nothing.

**The fix:** always-on for the audit function; storage moved onto the durable
tenant-scoped persistence layer (the same one S50 built), multi-replica-safe; wire
the already-coded daily Merkle root + inclusion proofs. Every commander audit — and
optionally every mutating tool action — appends `prev_hash → record_hash` with the
content-addressed artifact.

**Called by:** A's tools. **Read by:** the existing `/verify` + certificate endpoints.

### D. Deployment Profiles (cloud + on-prem)

**Purpose:** one codebase, two first-class deployment modes, selected by config.

- **Cloud profile:** hosted models (Groq/Gemini/OpenAI/Claude), managed multi-tenant
  storage, plus the identity/billing the SaaS work already built.
- **On-prem profile:** local models only (Ollama), self-contained durable storage and
  signing keys, **zero external API calls, no telemetry egress.**

**Mechanism:** `AURA_DEPLOYMENT_PROFILE=cloud|onprem`. A startup guard asserts the
active profile's constraints and **fails loud** if violated (e.g. on-prem with an
external-provider key configured) — the same fail-loud pattern as the existing
`auth_mode`/`jwt_enabled` production guards.

**Cross-cutting:** constrains A's model selection, B's card-building (must run
locally on-prem), and C's storage (local-durable on-prem). Includes an air-gapped
packaging pass (Helm/compose).

### E. Autonomous Standing-Work Engine

**Purpose:** "does it on its own, keeps everything ready." Flips the system from a
reactive assistant into a standing analyst.

**What it does:** on every data change or schedule, proactively (1) refreshes data
cards (B), (2) runs a configurable battery of standing analyses + aggregates,
(3) runs anomaly / drift / forensic *pre-scans*, and (4) writes results + a ranked
"attention queue" into the Results Cache. When the user asks, A usually **retrieves
and composes** rather than computes — the deepest latency win.

**Reuses (orchestration, not new machinery):** the `scheduler_service` distributed
queue (S20b), the UASR self-healing loop (S18/S41), and the audit function.

**Governance (mandatory):** an always-on autonomous layer is the one piece that can
hurt the user if ungoverned — cloud API spend, or on-prem compute saturation. So E
ships with **budget/compute caps** and **human-in-the-loop gating reusing the S41
risk-tiering**: it prepares and *proposes* freely; anything mutating waits for
approval.

**Writes:** the shared layer. **Gated by:** D (caps differ cloud vs on-prem).

### F. Command Surface (Terminal Cockpit, leveled up)

**Purpose:** the delivery surface where the prepared intelligence becomes one-click
reachable.

**What it does:** the dockview terminal (S46/S47) hosts the streaming commander and
surfaces E's outputs as live panels — a ranked **attention queue**, data-card views,
pipeline health, anomalies.

**The one-click rule (hard requirement):** everything observed is one click from its
full context. A flagged anomaly → its rows + the SQL that found it + the signed audit
cert, in one click, no nesting. This is the enforcement of the "no russian nesting
doll" principle across the whole surface.

**New capability on top of S46/S47 + Constellation:** a command palette, more
tool-backed panels, deep-linkable everything.

**Consumes:** A's stream + the shared layer (ready results). 

---

## End-to-end data flow (proactive + reactive)

1. **Prepare (proactive):** upload or schedule → E refreshes the Data Card (B) and
   runs standing analyses + pre-scans → results + attention queue land in the
   Results Cache.
2. **Deliver (reactive):** user asks in the cockpit (F) → Commander (A) loads the
   relevant cards + ready results → model loop → guarded tool calls → streamed answer.
3. **Audit:** any `run_audit` (or mutating tool) → forensic scan + Ed25519 cert →
   **appended to the durable ledger (C)**.
4. **Everywhere:** steps 1–3 run on cloud or local models per the active profile (D),
   and every observable is one click away in F.

---

## Build sequencing

Each subsystem ships incrementally; **no big-bang build.**

1. **A — thin vertical slice first:** `run_sql` only, streaming, running on **Ollama**
   — proves the air-gapped path *and* the latency win in one shot. Then widen the
   tool surface.
2. **C — in parallel:** independent of A, fixes a live validated gap.
3. **B — after A's loop exists:** improving what the loop reads pays off once the loop
   is real; includes the research pass.
4. **E — after A + B:** the standing-work engine needs the tools (A) and the cards (B)
   to orchestrate.
5. **F — incremental throughout:** each new capability gets its cockpit panel + the
   one-click wiring as it lands.
6. **D — baked into A/B/C/E from day one**, plus a final packaging pass.

---

## Risks & tradeoffs

- **Local model tool-calling is weaker.** The ReAct/JSON fallback and the
  deterministic tool-boundary guards carry the safety weight — the guardrails, not the
  model, keep it correct. On-prem quality will trail cloud; set expectations per
  profile.
- **Model-driven control flow is less deterministic** than the fixed DAG. Contained by
  validating at every tool boundary and constraining tool availability per context.
- **Autonomy is a cost/compute risk.** Mitigated by E's budget/compute caps + HITL
  gating (S41 risk-tiering). Fail toward *proposing*, never auto-acting on mutations.
- **Data-card novelty is unproven** until B's literature pass. Treat "invent a new
  representation" as a hypothesis to test, not a commitment.

---

## What gets its own spec next

This umbrella is the program map. The next document is the **Subsystem A** spec
(commander core + streaming + the `chat_with_tools` provider seam + the first
`run_sql` slice on Ollama), since A is the spine everything else plugs into.
