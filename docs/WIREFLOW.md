# AURA — App Wireflow

How a user actually moves through the product, and what happens behind each screen.
Renders as diagrams on GitHub.

---

## 1. Entry and authentication

```mermaid
flowchart LR
    A["/login"] -->|POST /auth/token| B{valid?}
    A --> S["/signup<br/>POST /auth/register"]
    S --> A
    B -->|no| A
    B -->|yes, JWT stored| W["/workbench<br/>(the app)"]
    X["/app/* legacy"] -.redirect.-> W
    P1["/certificate/:hash  PUBLIC"]
    P2["/verify/:hash  PUBLIC"]
```

`/certificate/:hash` and `/verify/:hash` are **public** — an external auditor opens them without
an AURA account. Everything else sits behind `ProtectedRoute`.

---

## 2. The workbench — one shell, many panels

```mermaid
flowchart TD
    W["Workbench shell<br/>topbar / sidebar / cockpit"]

    subgraph ANALYST
      A1["Ask AURA"]
      A2["Terminal"]
      A3["Dashboards"]
      A4["Library"]
      A5["Query History"]
    end
    subgraph ENGINEER
      E1["Connectors"]
      E2["Files and Data"]
      E3["Pipelines"]
      E4["Streaming"]
      E5["Scheduler"]
      E6["Lineage"]
      E7["Metadata Store"]
    end
    subgraph SCIENTIST
      S1["Counterfactuals"]
    end
    subgraph AUDITBRANCH
      D1["Audit Workbench"]
      D2["Exception Queue"]
      D3["Certificates"]
    end
    subgraph OPS
      O1["Healing Queue"]
      O2["Webhooks"]
      O3["Cost"]
    end

    W --> ANALYST
    W --> ENGINEER
    W --> SCIENTIST
    W --> AUDITBRANCH
    W --> OPS
```

Nav label to component mapping lives in `frontend/src/workbench/viewRegistry.ts`.
Every entry resolves to a real panel; there are no stubs left in the sidebar.

---

## 3. The core loop — a question becomes an answer

This is the product's primary path.

```mermaid
sequenceDiagram
    participant U as User
    participant UI as Ask AURA
    participant GW as Gateway /chat
    participant OR as LangGraph orchestrator
    participant DB as DuckDB lake

    U->>UI: "total amount by region"
    UI->>GW: POST /chat (Bearer token)
    GW->>OR: run_orchestrator
    OR->>OR: planner then sql_gen
    Note over OR: intent classifier sees REAL table and column<br/>schema, so it cannot invent fields
    OR->>DB: execute generated SQL
    DB-->>OR: columns and rows
    OR->>OR: verify (DPC cross-check, opt-in)
    Note over OR: same DuckDB connection as the answer,<br/>so it cannot manufacture mismatches
    OR->>OR: visualize then analyze
    OR-->>GW: sql, rows, chart, narrative, verdict
    GW-->>UI: ExecutionResult
```

**Verified:** a `GROUP BY` was computed independently in Python from the raw CSV and matched
AURA's answer exactly.

---

## 4. Data in — the engineer path

```mermaid
flowchart LR
    F["Upload CSV/Parquet/JSON"] --> ST["Storage backend<br/>local or S3, per-tenant"]
    ST --> PR["Profile and schema inference"]
    PR --> LAKE[("DuckDB lake")]
    C["External DB<br/>Postgres / MySQL / BigQuery"] -->|credentials encrypted| CONN["Connections"]
    CONN -.->|BROKEN end-to-end| LAKE
    LAKE --> Q["Queries / Pipelines / Dashboards"]
```

The dashed edge is the **known gap**: the gateway stores connections, but the connectors service
reads a different store that nothing writes to. See `STATUS.md`.

---

## 5. The audit branch — how a claim becomes evidence

```mermaid
flowchart TD
    L["Ledger / invoices / journal entries"] --> FA["Financial auditor<br/>AS-2110 materiality, AS-2201 three-way match<br/>AS-2305 expectations, AS-2401 Benford and cutoff"]
    FA --> FIND["Findings"]
    FIND --> EQ["Exception Queue<br/>human approves or overrides"]
    EQ -->|signed override| LEDG[("Audit ledger<br/>per-tenant hash chain")]
    FA -->|ED25519| CERT["Signed certificate"]
    CERT --> LEDG
    LEDG --> PROOF["Merkle inclusion proof<br/>plus signed tree head"]
    PROOF --> EXT["External auditor<br/>/jwks, /audit/sth, /audit/inclusion"]
```

The right-hand path is what makes the claim checkable **without trusting AURA's server**.

---

## 6. Keeping evidence valid under change

```mermaid
flowchart LR
    M["Monitor<br/>drift detection"] --> AN["Analyse"]
    AN --> PL["Plan a shim"]
    PL --> HQ["Healing Queue<br/>human decision"]
    HQ -->|approve or reject| WORM[("Signed override<br/>in WORM log")]
    HQ -->|approved| EX["Deploy shim"]
    EX --> M
```

Repairs are **never silent**. An automatic fix that changes model behaviour is itself an
auditable decision, so it needs a human owner and a signed record.

---

## Where things live

| Layer | Path |
|---|---|
| App shell and panels | `frontend/src/workbench/` |
| Nav to view mapping | `frontend/src/workbench/viewRegistry.ts` |
| API client | `frontend/src/services/api.ts` |
| Gateway routes | `aurabackend/api_gateway/routers/` |
| Agent DAG | `aurabackend/agents/langgraph_orchestrator.py` |
| Causal engine | `aurabackend/counterfactual_service/` |
| Ledger and signing | `aurabackend/shared/audit_ledger.py`, `counterfactual_service/signing.py` |
| Self-healing | `aurabackend/uasr/` |
