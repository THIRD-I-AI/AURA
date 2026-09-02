# AURA on AWS free tier — $0/month deploy

One `t3.micro` running the gateway, the React frontend, and Caddy for TLS.
No RDS, no Redis, no load balancer, no NAT gateway. Those four are what
normally turn a "small" AWS deploy into $50–100/month.

**Read the cost section before you start.** AWS is the free part; the LLM
provider is not.

---

## What actually runs, and what doesn't

The prod stack is eleven services plus Postgres plus Redis and needs ~2 GB.
A free-tier instance has **1 GB**. So this deploys a deliberately reduced
topology — and the reduction is honest about what it costs.

| Works | Why it survives the cut |
|---|---|
| Login / signup, JWT auth, tenant isolation | Gateway-native |
| **Ask AURA** — the core analyst loop (NL → SQL → answer → chart) | In-process agents + DuckDB |
| File upload → profile → query | Gateway-native, `/data/uploads` volume |
| Dashboards, saved queries, query history | Gateway-native, SQLite-backed |
| Financial audit + signed certificates | Runs **in-process inside the gateway** (see `CLAUDE.md`) |
| External verification: `/jwks`, signed tree head, Merkle proofs | Gateway-native |

| Won't work | Why |
|---|---|
| Counterfactual estimators (TMLE, DR-learner, IV-2SLS) | `base-runtime` image excludes dowhy/econml. Switch to `causal-runtime` **only** if you upsize the instance — the causal tier will not fit in 1 GB. |
| External-DB connectors | Already broken end-to-end before deploy — see the connectors gap in `STATUS.md`. Not a deploy regression. |
| Kafka streaming | Needs the `streaming-runtime` tier and a broker. |
| Distributed scheduler | Gateway now proxies it (`/api/v1/scheduler/*`), but scheduler_service isn't in this profile's docker-compose — the routes have nothing to talk to here. Live only on the full stack (`docker-compose.yml`). |

This table is the point of the file. A deploy that silently 500s on
Counterfactuals is worse than one that tells you up front it isn't included.

---

## Prerequisites

1. **A hostname that resolves to the instance.** Let's Encrypt cannot issue a
   certificate for a bare IP, and `shared/config.py` rejects an `http://` CORS
   origin in production — so there is no "just use the IP" shortcut. Free
   option: [DuckDNS](https://duckdns.org) gives you `yourname.duckdns.org`.
2. **Images published to GHCR.** `.github/workflows/cd.yml` builds and pushes
   `base-runtime` on a tag. The instance only ever *pulls* — never build on a
   t3.micro, it cannot compile pandas wheels in 1 GB.
3. AWS CLI authenticated (`aws sts get-caller-identity` returns your account).

---

## Step 1 — publish the images

```sh
git tag v0.1.0 && git push origin v0.1.0     # triggers cd.yml
```

This publishes four images for the release — `<version>-base`, `-causal`,
`-streaming`, and `-frontend`. The free-tier deploy uses `-base` and
`-frontend`.

**Leave the package private.** The backend image contains the full application
source, so flipping the package to public publishes the codebase to anyone who
runs `docker pull`. Instead create a read-only token at

    https://github.com/settings/tokens/new?scopes=read:packages

with **only** the `read:packages` scope, and put it in `.env` as `GHCR_TOKEN`.
Step 3 logs in with it.

Verify before provisioning — an anonymous manifest request must 403 (proving
the package is private) while an authenticated one returns 200:

```sh
curl -s -o /dev/null -w "%{http_code}\n" -u "$GHCR_USER:$GHCR_TOKEN" \
  -H "Accept: application/vnd.oci.image.index.v1+json" \
  https://ghcr.io/v2/<owner>/aura/manifests/0.1.1-base
```

## Step 2 — launch the instance

```sh
# Security group: SSH from your IP only, HTTP/HTTPS from anywhere.
aws ec2 create-security-group --group-name aura-sg \
  --description "AURA free tier" --query GroupId --output text
# -> sg-xxxx ; use it below

MYIP=$(curl -s https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --group-id sg-xxxx \
  --protocol tcp --port 22 --cidr ${MYIP}/32
aws ec2 authorize-security-group-ingress --group-id sg-xxxx \
  --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id sg-xxxx \
  --protocol tcp --port 443 --cidr 0.0.0.0/0

aws ec2 create-key-pair --key-name aura --query KeyMaterial \
  --output text > ~/.ssh/aura.pem && chmod 400 ~/.ssh/aura.pem

aws ec2 run-instances \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --instance-type t3.micro \
  --key-name aura --security-group-ids sg-xxxx \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=30,VolumeType=gp3}' \
  --user-data file://bootstrap.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=aura}]'
```

30 GB gp3 is the free-tier storage allowance — take all of it, the ML image
layers are large.

Point your DuckDNS record at the public IP now, **before** first boot, so
Caddy's certificate request succeeds on the first try. Let's Encrypt applies
an escalating backoff to repeated failures, which turns a 30-second mistake
into an hour of waiting.

## Step 3 — configure and start

```sh
ssh -i ~/.ssh/aura.pem ec2-user@<public-ip>
cd /opt/aura/deploy/aws-free-tier
cp .env.example .env && vi .env          # fill every blank; see the file's comments

# Authenticate to GHCR before the first pull — the images are private.
# --password-stdin keeps the token out of shell history and the process list.
set -a && . ./.env && set +a
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

docker compose up -d
docker compose logs -f api_gateway       # first boot is slow: ML imports
```

Then open `https://yourname.duckdns.org`.

---

## Cost — the honest version

| Item | Cost |
|---|---|
| t3.micro, 750 h/month | $0 while free-tier hours last |
| 30 GB gp3 EBS | $0 within the free-tier allowance |
| Data transfer at zero users | $0 |
| RDS / Redis / ALB / NAT | $0 — **not used, by design** |
| **LLM API calls** | **Not $0.** The real bill. |

Two things that will surprise you if nobody says them out loud:

- **Free-tier EC2 hours expire.** After the account's free-tier window closes,
  this instance becomes roughly $7–8/month. Set a billing alarm today:
  `aws budgets create-budget` or the console's Billing → Budgets. A $1 alert
  threshold is the cheapest insurance available.
- **One instance means one point of failure.** No autoscaling, no multi-AZ,
  and a stop/start changes the public IP unless you attach an Elastic IP
  (free *while attached to a running instance*, billed when idle). This is the
  correct trade for a zero-user demo and the wrong one for a customer.

## Operating it

```sh
docker compose logs -f api_gateway     # logs
./redeploy.sh                          # bring the box to origin/main + latest images (see below)
docker stats --no-stream               # memory headroom — the number that matters
```

### Redeploying

`AURA_TAG=latest` — `cd.yml` builds and pushes a fresh `latest-*` image on
every merge to `main`, but that alone never reaches this box: nothing here
auto-deploys. A full redeploy is really two independent steps (pull this repo
for `docker-compose.yml`/`Caddyfile` changes, then pull+recreate the
containers for new images) — doing only one half is exactly how the box went
15 days stale in 2026-08-31/09-02 (see `docs/BUG_REGISTRY.md` BUG-015: an
`.env` tag fix landed without the matching `git checkout`, so new services
and env vars silently couldn't exist). Use the script, which does both
atomically and refuses to leave a half-applied deploy:

```sh
./redeploy.sh                # deploy origin/main
./redeploy.sh v0.1.5         # deploy a specific tag/branch/commit
```

If the gateway OOM-loops, check `docker stats` first. The usual causes are a
`--workers` value above 1, or swapping to the `causal-runtime` image. Both
exceed 1 GB on their own.

### Backups

Every durable thing this deployment has — the four SQLite databases, the ED25519
signing keys, every uploaded dataset, and the tamper-evident audit hash chain —
lives in one unreplicated volume. `docker compose down -v`, a stray
`docker volume rm`, or an EBS failure loses all of it at once.

```sh
./backup.sh                                    # writes ./backups/<UTC timestamp>/
AURA_S3_BACKUP_BUCKET=my-bucket ./backup.sh    # also syncs off the box
```

Install it as a daily cron entry — a backup nobody runs is not a backup.
**Confirm `crontab` is even installed first** (`which crontab` — it was
missing entirely on the live box as of 2026-09-02, so this job had silently
never run despite being documented here; install via
`sudo dnf install -y cronie && sudo systemctl enable --now crond` on Amazon
Linux 2023). The path below must match wherever this repo is actually
checked out on the box (confirm with `pwd`, not this example):

```sh
15 3 * * * cd /opt/aura/deploy/aws-free-tier && ./backup.sh >> backup.log 2>&1
```

The databases are copied with SQLite's online backup API, not `cp`: copying a
live `.db` can capture a torn write and produce a file that opens fine and is
subtly corrupt. Each copy is then re-opened and `PRAGMA integrity_check`ed, so a
failed backup fails loudly instead of leaving a bad file you trust.
`uasr.duckdb` is the one exception — DuckDB has no online-backup API, so it is
copied as a plain file and is only crash-consistent. That is acceptable solely
because it holds drift baselines the detector relearns from live data; nothing
irreplaceable is in it.

To restore, stop the stack first — writing a database file under a live process
recreates the corruption you were avoiding, and the script refuses to run while
containers are up:

```sh
docker compose down
./backup.sh --restore 20260821T031500Z
docker compose up -d
```

### Rolling back a bad deploy

`.env` pins `AURA_TAG=latest` (moving), so rolling back means checking out the
last known-good git ref AND its matching image tag together — `redeploy.sh`
does both atomically, same as a forward deploy:

```sh
./redeploy.sh v0.1.4                              # last known-good release tag, or...
sed -i 's/^AURA_TAG=.*/AURA_TAG=v0.1.4/' .env && ./redeploy.sh v0.1.4   # if that tag's images aren't under `latest`
```
