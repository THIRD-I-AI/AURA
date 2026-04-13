<div align="center">

# AURA

### Autonomous Universal Research Analyst

**Enterprise-grade AI data analysis platform with multi-agent orchestration, universal database connectivity, streaming pipelines, and self-healing infrastructure.**

[![CI](https://github.com/THIRD-I-AI/AURA/actions/workflows/ci.yml/badge.svg)](https://github.com/THIRD-I-AI/AURA/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[Getting Started](#-getting-started) · [Architecture](#-architecture) · [Features](#-features) · [Agents](#-agent-framework) · [API Reference](#-api-reference)

</div>

---

## Overview

AURA is a microservices-based data analytics platform where 12 specialist AI agents collaborate to transform natural language questions into SQL queries, execute them securely, generate visualizations, and continuously improve through a self-healing feedback loop. It supports 13+ database types, real-time streaming pipelines, cron-based scheduling, and ships with a dark-themed React frontend.

```
┌────────────────────────────────────────────────────────────────────┐
│                     AURA PLATFORM OVERVIEW                         │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   User ──► React SPA ──► API Gateway (8000)                       │
│                              │                                     │
│              ┌───────────────┼───────────────────┐                 │
│              ▼               ▼                   ▼                 │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐        │
│   │  12 Specialist│  │  Connectors  │  │  Orchestration   │        │
│   │   AI Agents  │  │  (13+ DBs)   │  │  (DAG Executor)  │        │
│   └──────┬───────┘  └──────────────┘  └──────────────────┘        │
│          │                                                         │
│          ▼                                                         │
│   Code Gen ──► Sandbox ──► Insights ──► Visualization              │
│                                                                    │
│   Scheduler ◄──► Streaming Pipelines ◄──► UASR (Self-Healing)     │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## Key Highlights

| | |
|---|---|
| **Multi-Agent AI** | 12 specialist agents with DAG-based task execution and automatic LLM fallback (Groq → Gemini → Ollama → OpenAI) |
| **Universal Connectivity** | PostgreSQL, MySQL, SQLite, BigQuery, Snowflake, Redshift, MongoDB, ClickHouse, Cassandra, Databricks, DuckDB, Oracle, SQL Server |
| **Self-Healing (UASR)** | Drift detection, automatic recovery, semantic gating, rollback — all running autonomously |
| **Streaming Pipelines** | Real-time data pipelines with Kafka, backpressure handling, and stateful windowing |
| **SQL Safety** | Injection prevention, forbidden keyword blocking, complexity estimation, auto-LIMIT injection |
| **Evolution Engine** | Background improvement loop that analyzes failures, generates proposals via LLM, validates in sandbox, and auto-deploys |

---

## 🚀 Getting Started

### Prerequisites

- **Python** 3.11+
- **Node.js** 18+
- **Git**
- At least one LLM API key (Groq, Gemini, OpenAI) or local Ollama

### 1. Clone & Install

```bash
git clone https://github.com/THIRD-I-AI/AURA.git
cd AURA
```

**Backend:**

```bash
cd aurabackend
python -m venv .venv
# Windows: .venv\Scripts\activate | Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
```

**Frontend:**

```bash
cd frontend
npm install
```

### 2. Configure Environment

```bash
cp aurabackend/.env.example aurabackend/.env
```

Add your LLM provider key(s):

```env
# At least one required — AURA auto-detects and chains them with fallback
GROQ_API_KEY=your_key          # Fastest inference (free tier)
GEMINI_API_KEY=your_key        # Google Gemini
OPENAI_API_KEY=your_key        # OpenAI
OLLAMA_HOST=http://localhost:11434  # Local (no key needed)
```

### 3. Run All Services

**PowerShell (Windows):**

```powershell
cd aurabackend
.\start_all.ps1          # Starts all 9 backend services
```

**Frontend (separate terminal):**

```bash
cd frontend
npm run dev               # Vite dev server → http://localhost:5173
```

### 4. Docker (Production)

```bash
docker-compose up -d      # All services + health checks
docker ps                 # Verify container health
```

---

## 🏗 Architecture

AURA runs as **9 independent microservices**, each on its own port, communicating via REST/JSON. The frontend connects exclusively through the API Gateway.

| Port | Service | Description |
|------|---------|-------------|
| `5173` | **Frontend** | React 19 + TypeScript SPA (Vite) |
| `8000` | **API Gateway** | Central router — all client traffic enters here |
| `8001` | **Code Generation** | LLM-powered SQL/code generation from natural language |
| `8002` | **Connectors** | Universal database connectivity + Aura Vault hybrid storage |
| `8003` | **Execution Sandbox** | Secure SQL execution with serialization & error handling |
| `8004` | **Scheduler** | Cron-based job scheduling with retry & multi-channel alerts |
| `8005` | **Insights** | Auto-generated insights, anomaly detection, chart specs |
| `8006` | **Orchestration** | Agent coordination via TinyRecursive pattern (generator + critic) |
| `8007` | **Metadata Store** | Schema, execution history, embeddings persistence |
| `8009` | **UASR** | Self-healing layer — drift detection, recovery, rollback |

### Tech Stack

**Backend:** Python 3.11+ · FastAPI · SQLAlchemy 2.0 · Pydantic 2.7 · Pandas · aiokafka · pytest

**Frontend:** React 19 · TypeScript 5.9 · Vite · Recharts · Plotly.js · Chart.js

**Databases:** PostgreSQL · MySQL · SQLite · DuckDB · BigQuery · Snowflake · Redshift · MongoDB · ClickHouse · Cassandra · Databricks · Oracle · SQL Server

**LLM Providers:** Groq (Llama 3.3 70B) · Google Gemini 2.5 Flash · OpenAI GPT-4o-mini · Ollama (local)

**Infrastructure:** Docker Compose · GitHub Actions CI · MCP Protocol

---

## ✨ Features

### Natural Language to SQL

Ask questions in plain English. AURA's agent pipeline classifies your intent, generates validated SQL, executes it in a sandboxed environment, and returns results with auto-generated visualizations.

### 12 Specialist Agents

Every query passes through a DAG of specialist agents, each responsible for one stage of the pipeline:

| Agent | Role |
|-------|------|
| **IntentAgent** | Classifies input as SQL query or conversation — fast-path gateway |
| **IngestionAgent** | File uploads, DB connections, data profiling |
| **SchemaArchitectAgent** | Schema inspection, table creation, index recommendations |
| **SQLGeneratorAgent** | Natural language → SQL with schema awareness and EXPLAIN validation |
| **ExecutionAgent** | Secure SQL execution with result serialization |
| **TransformAgent** | Joins, aggregations, window functions, deduplication |
| **QualityAgent** | Null rates, uniqueness, range checks, regex, custom SQL validation |
| **AnalysisAgent** | Statistical analysis, outlier detection (pure Python, no pandas) |
| **OptimizationAgent** | Query tuning — indexes, partitioning, materialized views |
| **VisualizationAgent** | Optimal chart type selection, Recharts JSON spec generation |
| **PipelineAgent** | ETL/ELT pipeline building and scheduler integration |
| **MonitorAgent** | Pipeline health, data quality monitoring, UASR trigger |

### LLM Fallback Chain

AURA automatically detects all configured LLM providers and chains them with cascading fallback. If Groq hits a rate limit or payload size error, the request transparently retries on Gemini, then Ollama, then OpenAI — no user intervention required.

```
Groq (fast) ──► Gemini ──► Ollama (local) ──► OpenAI
     ↓ on error     ↓ on error     ↓ on error
```

### UASR — Self-Healing Layer

**Universal Agentic Semantic Recovery** monitors data quality in real time:

- **Drift Detection** — IQR-based statistical analysis identifies schema and value drift
- **Automatic Recovery** — Actuator agent generates and deploys correction shims
- **Semantic Gating** — Embedding-based similarity checks against reference baselines
- **Rollback** — One-call rollback of deployed shims if recovery makes things worse
- **Observability** — Full metrics dashboard: healing events, recovery success rates, drift history

### Streaming Pipelines

Real-time data processing powered by Kafka:

- Configurable sources and sinks
- Backpressure handling
- Stateful windowing (tumbling, sliding, session)
- Pipeline DSL generation from natural language

### Scheduler Service

Automate recurring analysis with cron-based scheduling:

- Retry logic with exponential backoff
- Job dependency chains (`depends_on`)
- Multi-channel notifications: Email (SMTP), Slack webhooks, generic webhooks
- Failure actions: `notify`, `retry`, `skip_dependents`

### SQL Safety & Validation

Every query passes through `SQLSafetyValidator` before execution:

- Blocks `DROP`, `DELETE`, `TRUNCATE`, `ALTER`, `INSERT`, `UPDATE`, `EXEC`
- Detects SQL injection patterns (UNION exploits, `xp_`/`sp_` stored procedures)
- Performance warnings for `SELECT *`, leading wildcards, complex JOINs
- Auto-injects `LIMIT` clauses for result set protection
- Supports dry-run mode via `EXPLAIN` or `LIMIT 0`

### Evolution Engine

A background improvement loop that makes AURA smarter over time:

1. Analyzes failure patterns and slow executions
2. Generates improvement proposals via LLM
3. Validates proposals in a sandboxed environment
4. Auto-deploys when confidence ≥ 0.75
5. Full audit trail via `SystemEvolutionLog`

### Insights Engine

Automatic insight generation from query results:

- **Insight Types:** Trend, Anomaly, Comparison, Distribution, Correlation, Outlier
- **Chart Types:** Table, Line, Bar, Scatter, Pie, Histogram, Box, Heatmap
- Statistical analysis (mean, median, std, quartiles, IQR outlier detection)
- Correlation detection across columns
- Chart spec generation compatible with Recharts and Plotly

### Frontend

Dark-first design system inspired by Linear, Vercel, Grafana, and Datadog:

- **5 Modes:** Chat, Database, Visualization, Strategic, Pipelines
- **Design Tokens:** 51+ CSS custom properties, 8 status colors, 9 font sizes, 4 shadow levels
- **Typography:** Inter (UI) + JetBrains Mono (code)
- **Components:** Chat interface, file upload, live dashboard, streaming panel, agent panel, query history, settings
- **Charts:** Recharts, Plotly.js, Chart.js — auto-selected based on data shape

---

## 🗄 Database Connectivity

Connect to any of the supported databases through the Connectors Service or the Aura Vault hybrid storage layer:

| Category | Databases |
|----------|-----------|
| **SQL** | PostgreSQL, MySQL, SQLite, DuckDB |
| **Cloud Warehouses** | BigQuery, Snowflake, Redshift |
| **NoSQL** | MongoDB, Cassandra |
| **Analytics** | ClickHouse, Databricks |
| **Enterprise** | Oracle, SQL Server |

**Aura Vault** provides a hybrid multimodal storage backend (PostgreSQL or DuckDB) for AURA's internal data — schemas, execution history, and metadata.

---

## 📡 API Reference

All services expose interactive Swagger docs at `/docs` when running.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (all services) |
| `/v1/chat` | POST | Natural language query → results |
| `/v1/connect` | POST | Establish database connection |
| `/v1/execute` | POST | Execute SQL in sandbox |
| `/v1/pipelines` | GET/POST | Pipeline CRUD |
| `/v1/schedule` | POST | Schedule a recurring job |
| `/uasr/ingest` | POST | Submit micro-batch for drift detection |
| `/uasr/drift/status` | GET | List drift events |
| `/uasr/metrics` | GET | Healing observability dashboard |
| `/uasr/rollback` | POST | Rollback deployed shims |

Full API docs: `http://localhost:8000/docs` (API Gateway)

---

## 🧪 Testing

```bash
cd aurabackend

# Run all tests
pytest tests/ --tb=short -q

# Run specific test suites
pytest tests/test_safety.py       # SQL injection prevention
pytest tests/test_agents.py       # Agent framework
pytest tests/test_uasr.py         # Self-healing layer
pytest tests/test_streaming.py    # Streaming pipelines
pytest tests/test_pipeline.py     # Pipeline engine
```

**CI/CD** runs on every push and PR via GitHub Actions:

| Job | What it checks |
|-----|---------------|
| **Backend Tests** | pytest across Python 3.11 & 3.12 |
| **Frontend Typecheck** | `tsc --noEmit` |
| **Frontend Lint** | ESLint |
| **Backend Lint** | Ruff |

---

## ⚙️ Configuration

### Environment Variables

<details>
<summary>Click to expand full environment variable reference</summary>

**LLM Providers:**

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | Groq API key |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `GEMINI_API_KEY` | — | Google Gemini key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model |
| `OPENAI_API_KEY` | — | OpenAI key |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |

**Services:**

| Variable | Default | Description |
|----------|---------|-------------|
| `API_GATEWAY_PORT` | `8000` | API Gateway port |
| `CODE_GENERATION_SERVICE_PORT` | `8001` | Code Gen port |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | CORS whitelist |
| `DATABASE_URL` | `sqlite:///./aura.db` | Default database |
| `EXECUTION_TIMEOUT_SECONDS` | `15` | Query timeout |
| `SCHEDULER_CHECK_INTERVAL` | `60` | Scheduler poll (seconds) |

**Alerts:**

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_HOST` | — | Email server host |
| `SLACK_WEBHOOK_URL` | — | Slack notifications |
| `ALERT_WEBHOOK_URL` | — | Generic webhook |

**Security:**

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | — | JWT signing key |
| `ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Token expiry |

</details>

---

## 📁 Project Structure

```
AURA/
├── frontend/                    # React 19 + TypeScript SPA
│   ├── src/
│   │   ├── components/          # UI components (Chat, Upload, Charts, Layout)
│   │   ├── pages/               # App pages (Agent, Pipelines, Streaming, Settings)
│   │   └── App.tsx              # Root with theme provider & routing
│   └── vite.config.ts
│
├── aurabackend/                 # Python microservices
│   ├── agents/                  # 12 specialist agents + base classes
│   │   └── specialists/         # Individual agent implementations
│   ├── api_gateway/             # Central API router (port 8000)
│   ├── code_generation_service/ # LLM-powered code gen (port 8001)
│   ├── connectors/              # Database connectors (port 8002)
│   ├── execution_sandbox/       # Secure SQL execution (port 8003)
│   ├── scheduler_service/       # Cron scheduler (port 8004)
│   ├── insights/                # Auto-insights engine (port 8005)
│   ├── orchestration_service/   # Agent orchestration (port 8006)
│   ├── metadata_store/          # Schema & history persistence (port 8007)
│   ├── uasr/                    # Self-healing layer (port 8009)
│   ├── pipeline/                # Streaming pipeline engine
│   ├── mcp_core/                # Model Context Protocol server
│   ├── safety/                  # SQL validation & injection prevention
│   ├── shared/                  # Config, LLM provider, middleware, utils
│   └── tests/                   # pytest test suites
│
├── docker-compose.yml           # Production container orchestration
├── ARCHITECTURE.md              # Detailed architecture diagrams
└── .github/workflows/ci.yml    # CI pipeline
```

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make changes and add tests
4. Ensure CI passes: `pytest tests/ --tb=short -q`
5. Submit a pull request

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<div align="center">

Built by [THIRD-I-AI](https://github.com/THIRD-I-AI)

</div>
