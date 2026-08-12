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
| Distributed scheduler | Standalone service, no gateway route yet (`STATUS.md`). |

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

Then make both packages public (Settings → Packages → Change visibility), or
the instance needs a GHCR pull secret.

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
docker compose pull && docker compose up -d   # deploy a new tag
docker stats --no-stream               # memory headroom — the number that matters
```

If the gateway OOM-loops, check `docker stats` first. The usual causes are a
`--workers` value above 1, or swapping to the `causal-runtime` image. Both
exceed 1 GB on their own.
