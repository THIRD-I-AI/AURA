# AURA Deployment Guide

How to deploy AURA for the three targets the product must serve:

| Target | Where it runs | LLM inference | Egress |
|--------|---------------|---------------|--------|
| **A. Cloud** | Public/managed SaaS — k8s (Helm) or a single VM (Compose) | Cloud API (Groq / Gemini / OpenAI) | Open |
| **B. Semi-cloud / hybrid** | App in cloud or a customer DMZ | Customer-hosted OpenAI-compatible endpoint | Allowlisted to the endpoint + DB |
| **C. On-prem / air-gapped** | Entirely inside the customer network | Fully local (Ollama) | None |

Two mechanisms cover all three:

- **Docker Compose** — `docker-compose.yml` (dev base) + `docker-compose.prod.yml`
  (prod overrides). Best for a single host: one pilot VM, an on-prem box, a laptop.
- **Helm** — `deploy/helm/aura/`. Best for Kubernetes at multi-customer scale,
  with HPA / PDB / NetworkPolicy / TLS ingress / Prometheus.

The target is mostly a **configuration choice**, not a different build: the same
images, the same durable-state model, the same armed auth gates. Only the LLM
wiring and the egress posture change between A, B, and C — see
[The LLM mode matrix](#the-llm-mode-matrix).

---

## The shared foundation (applies to every target)

### 1. Secrets and environment

Copy `aurabackend/.env.prod.example` to a repo-root `.env` and fill it in. Boot
**fails closed** without the first four; the rest degrade features.

| Var | Purpose | Generate |
|-----|---------|----------|
| `DB_PASSWORD` | Postgres password (db service + every backend DSN) | strong random |
| `SECRET_KEY` | JWT session signing secret (default value is rejected in prod) | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `AURA_SIGNING_PRIVATE_KEY_HEX` | **Stable** ed25519 audit-cert signing key — generate once, never rotate (a new key invalidates every issued certificate) | `python -c "import os; print(os.urandom(32).hex())"` |
| `AURA_PII_TOKEN_KEY` | Deterministic PII tokenization salt | `python -c "import os; print(os.urandom(32).hex())"` |
| LLM vars | See [the matrix](#the-llm-mode-matrix) | — |

Never commit real values. `.env.prod.example` carries names + guidance only.

### 2. Armed auth gates (the multi-tenant boundary)

Production **must** run with both of these on — they are what make every data
request authenticated and tenant-scoped (S42/S43):

- `AURA_JWT_ENABLED=true` — the master switch that installs `JWTAuthMiddleware`
  (`shared/service_factory.py`). **It defaults to `false` and is _not_ implied
  by `ENVIRONMENT=production`.** Without it the app boots clean but enforces no
  auth, and tenant isolation collapses to a single shared `default` bucket.
- `AURA_AUTH_MODE=password` — validates credentials when minting/checking JWTs.
  `open` mode is rejected at boot in production.

The prod Compose sets both in its `x-backend-env` anchor; the Helm chart pins
both in `values.env`. If you write your own overlay, do not drop them.

### 3. Durable state

| State of record | Backing | Notes |
|-----------------|---------|-------|
| Users, datasets metadata, gateway state, scheduler, UASR | **Postgres** | `DATABASE_URL` / `METADATA_DATABASE_URL` / `SCHEDULER_DATABASE_URL` (`postgresql+asyncpg://…`). S43 verified zero SQLite-isms. |
| Uploaded CSVs | **Durable volume** at `AURA_UPLOADS_ROOT` (`/data/uploads`) | Per-tenant subdirs `data/uploads/<tenant>/` (S42). |
| Audit certificates | Postgres + the **stable** signing key | Survives restarts → certs keep verifying. |
| Query engine (DuckDB) | **In-memory by design** | Materializes the durable CSVs on the fly; not a store of record, needs no durability. |

### The LLM mode matrix

AURA's LLM layer is provider-agnostic and portable (S44). Set these in `.env`
(Compose — they flow to every backend via `env_file: .env`) or in
`values.env` / the secret (Helm). Pin one backend explicitly in production:

| Mode | Vars | Used by target |
|------|------|----------------|
| **Cloud API** | `AURA_LLM_PROVIDER=groq` (or `gemini`/`openai`) + the matching `GROQ_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY` | A |
| **Customer-hosted endpoint** | `AURA_LLM_PROVIDER=openai` + `OPENAI_BASE_URL=http://your-host:8001/v1` (alias `AURA_LLM_BASE_URL`); key optional for self-hosted servers | B |
| **Fully local / air-gapped** | `AURA_LLM_PROVIDER=ollama` + `OLLAMA_BASE_URL=http://ollama:11434` + `OLLAMA_MODEL=llama3.1` | C |

Leave `AURA_LLM_PROVIDER` unset to auto-detect in `AURA_LLM_PROVIDERS` order
(default `groq,gemini,ollama,openai`, first available wins) — fine for dev,
not recommended for a pinned production deploy.

---

## Target A — Cloud (public / managed SaaS)

Cloud LLM API, public URL, durable managed state.

### Option A1 — Compose on a single VM (fastest pilot)

```bash
# on the VM, with Docker + the compose plugin installed
git clone <repo> && cd Data-Analyst-Agent
cp aurabackend/.env.prod.example .env        # then fill it in (see foundation §1)

# LLM: pin a cloud provider in .env
#   AURA_LLM_PROVIDER=groq
#   GROQ_API_KEY=...

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

This brings up Postgres (`db`, durable `aura-pgdata` volume), all backends on
the armed gates, the uploads volume (`aura-uploads`), and the nginx frontend on
port **80**. The gateway is on **8000**.

**TLS / a real URL:** terminate TLS in front of the frontend with a reverse
proxy. A one-liner with Caddy (auto-Let's-Encrypt):

```caddyfile
aura.your-company.com {
    reverse_proxy localhost:80
}
```

### Option A2 — Helm on managed Kubernetes (scale)

```bash
kubectl create namespace aura

# Secrets live in a K8s Secret you create out-of-band (sops / sealed-secrets /
# external-secrets). The chart references it by name (envSecretName: aura-secrets).
kubectl create secret generic aura-secrets -n aura \
  --from-literal=SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))") \
  --from-literal=AURA_SIGNING_PRIVATE_KEY_HEX=$(python -c "import os;print(os.urandom(32).hex())") \
  --from-literal=AURA_PII_TOKEN_KEY=$(python -c "import os;print(os.urandom(32).hex())") \
  --from-literal=GROQ_API_KEY=... \
  --from-literal=DATABASE_URL='postgresql+asyncpg://USER:PASS@your-managed-pg:5432/aura'

helm install aura ./deploy/helm/aura -n aura \
  --set image.backend.repository=ghcr.io/your-org/aura-backend \
  --set image.frontend.repository=ghcr.io/your-org/aura-frontend \
  --set image.backend.tag=$(git rev-parse --short HEAD) \
  --set image.frontend.tag=$(git rev-parse --short HEAD) \
  -f values.cloud.yaml
```

`values.cloud.yaml` overlay:

```yaml
env:
  AURA_LLM_PROVIDER: groq      # cloud API; key comes from the secret
ingress:
  enabled: true
  className: nginx
  host: aura.your-company.com
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  tls:
    enabled: true
    secretName: aura-tls
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
```

> Use an **external managed Postgres** (Neon, RDS, Cloud SQL) via `DATABASE_URL`
> in the secret — keep `persistence.enabled: false`. The chart's optional `persistence`
> PVC is only for an embedded store, which production should not use.
>
> **Uploads:** the chart provisions a durable per-tenant uploads PVC by default
> (`uploads.enabled: true`, mounted on `api_gateway` at `/data/uploads`). It is
> `ReadWriteOnce`, so with the default 2 gateway replicas both pods must land on
> one node. To scale the gateway across nodes, set `uploads.accessModes:
> [ReadWriteMany]` with an RWX storage class, run `backendServices.api_gateway.replicas: 1`,
> or move uploads to object storage (the tracked enterprise-scale item).

The chart's default `values.env` already pins `ENVIRONMENT=production`,
`AURA_AUTH_MODE=password`, and `AURA_JWT_ENABLED=true`, so the auth/tenant gates
are armed out of the box.

---

## Target B — Semi-cloud / hybrid

The AURA app runs in your cloud (or a customer DMZ), but **inference stays under
the customer's control**: AURA calls a customer-hosted OpenAI-compatible
endpoint (vLLM, LM Studio, Azure OpenAI, an existing internal LLM gateway, or an
on-prem inference box). No prompt/data leaves the customer boundary for the LLM.

Deploy exactly as in Target A (Compose or Helm) — only the LLM wiring and egress
change.

**Compose** — in `.env`:

```dotenv
AURA_LLM_PROVIDER=openai
OPENAI_BASE_URL=https://llm.customer.internal/v1   # their OpenAI-compatible endpoint
# OPENAI_API_KEY=...        # only if their endpoint requires one
OPENAI_MODEL=their-served-model-name
```

**Helm** — `values.hybrid.yaml`:

```yaml
env:
  AURA_LLM_PROVIDER: openai
  OPENAI_BASE_URL: https://llm.customer.internal/v1
  OPENAI_MODEL: their-served-model-name
networkPolicy:
  enabled: true
  allowAllEgress: false
  egressAllowedCIDRs:
    - cidr: 10.50.0.0/24        # the customer LLM endpoint
    - cidr: 10.60.0.0/24        # managed Postgres, if external
```

Put `OPENAI_API_KEY` in the secret (`aura-secrets`) only if the endpoint needs
one. Data-of-record can also stay customer-managed: point `DATABASE_URL` at
their Postgres. This is the "their model + their data, our app" posture.

---

## Target C — On-prem / air-gapped

Everything inside the customer network, **no egress**: local Postgres, local
inference via Ollama, locked-down networking. Cloud keys are not used.

### Prerequisites — get the images into the airgap

There is no registry pull inside the airgap, so transfer everything as tarballs.
The published `aura-backend` / `aura-frontend` images (from the CD pipeline) plus
the third-party images:

```bash
# on a connected machine
docker save ghcr.io/your-org/aura-backend:TAG ghcr.io/your-org/aura-frontend:TAG \
            postgres:16 ollama/ollama:latest -o aura-images.tar
# move aura-images.tar across the boundary, then on the air-gapped host:
docker load -i aura-images.tar
```

Because `docker-compose.prod.yml` *builds* the backend/frontend from source, an
air-gapped host should reference the loaded images instead of rebuilding — add a
small `docker-compose.images.yml` overlay that sets `image:` (and an empty
`build: {}`) for `api_gateway`, the other backends, and `frontend`, pointing at
the tags you loaded. (Alternatively, mirror the published images into an internal
registry and reference that.) The model weights must also be local — see the
`ollama pull` step below.

### Option C1 — Compose with a bundled Ollama service

The shipped compose files have no Ollama service; add this small overlay
`docker-compose.ollama.yml`:

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    restart: unless-stopped
    volumes:
      - ollama-models:/root/.ollama
    # no ports needed — reached over the internal aura-network as http://ollama:11434

volumes:
  ollama-models:
```

`.env`:

```dotenv
AURA_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=llama3.1
# OLLAMA_NUM_CTX=8192   # default; AURA caps the context so the model loads on
#                       # constrained boxes (Ollama otherwise opens a model at its
#                       # full 128k window and the KV cache OOMs). Lower to 4096/2048
#                       # on small/edge hardware; raise on a big GPU box.
```

Bring it up with all three files:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  -f docker-compose.ollama.yml up -d
# load the model into the running Ollama (first boot only):
docker compose exec ollama ollama pull llama3.1
```

Postgres runs in-stack on the durable `aura-pgdata` volume — nothing leaves the
host.

### Option C2 — Helm with in-cluster Ollama

The chart does not bundle an LLM engine; run Ollama as its own in-cluster
workload (e.g. the community `otwld/ollama-helm` chart, or a plain
Deployment + Service + PVC for the model weights) and point AURA at its Service.

`values.airgap.yaml`:

```yaml
env:
  AURA_LLM_PROVIDER: ollama
  OLLAMA_BASE_URL: http://ollama.aura.svc.cluster.local:11434
  OLLAMA_MODEL: llama3.1
networkPolicy:
  enabled: true
  allowAllEgress: false        # zero external egress
  egressAllowedCIDRs: []       # in-cluster traffic only (DNS/Postgres/Ollama)
ingress:
  enabled: true                # internal hostname only, no public DNS
  className: nginx
  host: aura.internal
```

Use an on-prem/internal Postgres via `DATABASE_URL` in the secret, or the
chart's PVC-backed embedded store if no Postgres is available on site.

---

## Verify any deployment

1. **Health:** every backend exposes `/health`; the gateway probe hits
   `http://<host>:8000/health`.
2. **Core flows on the real backing store** — register → login → upload → chat→SQL:

   ```bash
   AURA_BASE=http://<host>:8000 python aurabackend/tests/smoke_postgres.py --skip-chat
   # drop --skip-chat once the LLM mode is wired to exercise chat→SQL end to end
   ```

3. **Durability / signing key:** sign an audit certificate, **restart the
   stack**, then re-verify the certificate — it must still validate (proves the
   stable signing key + Postgres durability survived the restart).
4. **Tenant isolation:** a second tenant must not see the first tenant's
   uploads or tables (S42 — depends on the armed gates from the foundation §2).

## See also

- `aurabackend/.env.prod.example` — every prod var, with generation guidance.
- `deploy/helm/aura/README.md` — chart options (HPA, PDB, NetworkPolicy, PVC,
  ServiceMonitor, the TRAIGA audit WORM shipper + chain verifier).
- `ENTERPRISE.md` — reference topology, sizing guidance, and the Sec-7
  production go-live checklist (PII masking, rate limits, audit, TLS).
- `docs/SAAS_ROADMAP.md` — the broader productionization roadmap.
