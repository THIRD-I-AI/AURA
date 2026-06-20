# AURA Helm chart

A Helm chart that deploys AURA's 9 backend services + the frontend onto Kubernetes.
It mirrors what `docker-compose.prod.yml` does locally.

> **v0.1.0 — first cut.** Includes Deployments, Services, optional Ingress,
> ConfigMap, ServiceAccount, **HPA, PDB, NetworkPolicy, PVC, and Prometheus
> Operator ServiceMonitor** templates. All backend pods are stateless by
> default; persistent data should live in an external Postgres (referenced
> via `envSecretName`). Enable `persistence` only if you're running an
> embedded store inside `metadata_store`.

## Quick start

```bash
# 1. Create the secret with your provider keys (rotate periodically).
kubectl create namespace aura
kubectl create secret generic aura-secrets \
  --from-literal=GROQ_API_KEY=... \
  --from-literal=GEMINI_API_KEY=... \
  --from-literal=DATABASE_URL=postgresql://... \
  -n aura

# 2. Install the chart.
helm install aura ./deploy/helm/aura \
  --namespace aura \
  --set image.backend.tag=$(git rev-parse --short HEAD) \
  --set image.frontend.tag=$(git rev-parse --short HEAD)

# 3. Reach the gateway (no ingress configured by default).
kubectl port-forward -n aura svc/aura-aura-api-gateway 8000:8000
```

## What gets installed

| Service                  | Port | Default replicas |
|--------------------------|------|------------------|
| api_gateway              | 8000 | 2                |
| code_generation_service  | 8001 | 1                |
| connector_service        | 8002 | 1                |
| execution_sandbox        | 8003 | 1                |
| scheduler_service        | 8004 | 1                |
| insights_service         | 8005 | 1                |
| orchestration_service    | 8006 | 1                |
| metadata_store           | 8007 | 1                |
| uasr_service             | 8009 | 1                |
| frontend                 | 80   | 2                |

Each backend service:
- shares the same backend image (different `uvicorn` args)
- exposes `/health` (Kubernetes probes hit it)
- pulls non-secret env from a ConfigMap and secret env from `envSecretName`
- runs as non-root (UID 1000) with `allowPrivilegeEscalation: false` and dropped capabilities

## Common overrides

```yaml
# values.production.yaml
image:
  backend:
    repository: ghcr.io/your-org/aura-backend
    tag: v1.4.2
  frontend:
    repository: ghcr.io/your-org/aura-frontend
    tag: v1.4.2

backendServices:
  api_gateway:
    replicas: 4
    resources:
      requests: { cpu: "500m", memory: "1Gi" }
      limits:   { cpu: "2",    memory: "2Gi" }

ingress:
  enabled: true
  className: nginx
  host: aura.your-company.com
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  tls:
    enabled: true
    secretName: aura-tls
```

## Validating before push

This chart ships with a tiny render-test that catches structural YAML bugs without
needing a Helm install:

```bash
python deploy/helm/aura/tests/render_test.py
```

When `helm` is available, also run:

```bash
helm lint deploy/helm/aura
helm template aura deploy/helm/aura | kubectl apply --dry-run=client -f -
```

## Optional features (off by default — flip to enable)

```yaml
autoscaling:
  enabled: true              # HPA per backend service
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  frontend:
    enabled: true            # frontend HPA is independent

podDisruptionBudget:
  enabled: true              # one PDB per service with replicas > 1
  minAvailable: 1

networkPolicy:
  enabled: true              # deny-all + frontend→gateway + gateway→backends
  ingressNamespaces:         # additional namespaces allowed to reach gateway
    - matchLabels:
        kubernetes.io/metadata.name: monitoring
  allowAllEgress: true       # set false to lock egress down to known endpoints

persistence:
  enabled: true              # PVC mounted into the metadata_store pod
  storageClass: gp3          # "" to use the cluster default
  size: 20Gi
  mountPath: /data
  service: metadata_store    # which backendServices key gets the volume

serviceMonitor:
  enabled: true              # one ServiceMonitor per backend → Prom Operator
  labels:                    # match your Prometheus's serviceMonitorSelector
    release: prometheus
  interval: 30s
  scrapeTimeout: 10s
  path: /metrics
```

Per-service HPA overrides nest under `backendServices.<name>.autoscaling`:

```yaml
backendServices:
  api_gateway:
    autoscaling:
      maxReplicas: 20
      targetCPUUtilizationPercentage: 60
```

## Object storage (recommended for multi-replica)

By default the gateway writes uploaded CSVs to a local PVC (`uploads.enabled: true`,
`ReadWriteOnce`). With two or more gateway replicas on different nodes the second pod
cannot mount the same RWO volume and stays Pending.

**For true multi-node scale use object storage (S45):**

```yaml
# values.s3.yaml
env:
  AURA_STORAGE_BACKEND: s3
  AURA_S3_BUCKET: aura-uploads
  AURA_S3_ENDPOINT_URL: ""        # blank for AWS S3; set for R2/MinIO/on-prem
  AURA_S3_REGION: us-east-1
  AURA_S3_URL_STYLE: path         # path for MinIO; vhost for AWS
  AURA_S3_USE_SSL: "true"

uploads:
  enabled: false                  # PVC not needed — object storage is the durable store
```

Add `AURA_S3_ACCESS_KEY_ID` and `AURA_S3_SECRET_ACCESS_KEY` to the `aura-secrets`
Secret (not in values.yaml). On EKS/GKE you can omit them and use IRSA/Workload Identity
instead.

**Air-gapped / on-prem MinIO:** the AURA image bundles the DuckDB `httpfs` extension
(installed at build time — no outbound network call at runtime). Point
`AURA_S3_ENDPOINT_URL` at an in-cluster or on-host MinIO service:

```yaml
env:
  AURA_STORAGE_BACKEND: s3
  AURA_S3_ENDPOINT_URL: http://minio.minio.svc.cluster.local:9000
  AURA_S3_URL_STYLE: path
  AURA_S3_USE_SSL: "false"
```

## Roadmap

v0.1 ships everything the Sprint 6 plan called for (Deployments, Services,
Ingress, HPA, PDB, NetworkPolicy, PVC, ServiceMonitor). The natural next
chart additions are: Pod-level `topologySpreadConstraints` for multi-zone
clusters, `Job`/`CronJob` templates if AURA grows scheduled batch work,
and an opt-in `OpenTelemetry Collector` sidecar pattern.
