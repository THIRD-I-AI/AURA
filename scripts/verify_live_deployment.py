"""
Live black-box verification of every promised AURA feature.

Why this exists: unit tests prove the *code* behaves correctly against
mocks; they cannot prove the *deployed* system works for a real user,
because they never touch the real config, real database, real auth flow,
or a real LLM call. On 2026-08-31, live testing against a staged AWS
deployment found two real bugs in UASR that had passed 2000+ unit tests
for a year (see docs/superpowers/specs/2026-08-31-uasr-live-validation-
and-benchmark.md and docs/BUG_REGISTRY.md). This script generalizes that
discipline to every promised feature, not just UASR, so future work builds
on a known-good baseline instead of stacking on undiscovered breakage.

Scope: `deploy/aws-free-tier/README.md` already states which capabilities
are promised on THIS deployment vs. explicitly excluded (no causal-runtime
estimators, no external-DB connectors, no Kafka streaming, no distributed
scheduler) -- that table is the scope, not a guess. Checks for excluded
capabilities are not included here at all, rather than reported as
failures.

Every check either reads, or writes to a clearly `verify_`-namespaced
resource (dashboard, webhook, UASR source) that is safe to leave behind --
nothing here deletes or mutates another user's data. Checks run
sequentially with a short pause between them: this is a 1GB single-
instance free-tier box, and the point of this script is to rule out
degradation, not cause it.

Usage:
    STAGING_EMAIL=... STAGING_PASSWORD=... python scripts/verify_live_deployment.py
    python scripts/verify_live_deployment.py --url https://dataaura.duckdns.org

Credentials are read from env vars only -- never hardcoded, never printed,
never written to the output report.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

import httpx

DEFAULT_URL = "https://dataaura.duckdns.org"
V1 = "/api/v1"
PACE_SECONDS = 0.5  # between checks -- see module docstring


class SkipCheck(Exception):
    """Raised by a check function to mark itself skipped, not failed."""


@dataclass
class CheckResult:
    name: str
    status: str  # pass | fail | skip
    detail: str = ""
    latency_ms: float = 0.0


@dataclass
class Verifier:
    base_url: str
    email: str
    password: str
    client: httpx.Client = field(init=False)
    token: Optional[str] = field(default=None, init=False)
    results: List[CheckResult] = field(default_factory=list)
    _run_ns: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def __post_init__(self) -> None:
        self.client = httpx.Client(base_url=self.base_url, timeout=30.0)

    def _auth_headers(self) -> dict:
        if not self.token:
            raise SkipCheck("no auth token (login check did not run or failed)")
        return {"Authorization": f"Bearer {self.token}"}

    def ns(self, label: str) -> str:
        """A resource name namespaced to this run -- identifiable, safe to
        leave on the live box, distinguishable across separate runs."""
        return f"verify_{self._run_ns}_{label}"

    def run(self, name: str, fn: Callable[[], Optional[str]]) -> CheckResult:
        start = time.perf_counter()
        try:
            detail = fn() or ""
            status = "pass"
        except SkipCheck as exc:
            status, detail = "skip", str(exc)
        except Exception as exc:  # noqa: BLE001 -- a check failure IS the signal
            status, detail = "fail", f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - start) * 1000
        result = CheckResult(name, status, detail, latency_ms)
        self.results.append(result)
        icon = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
        suffix = f" -- {detail}" if detail and status != "pass" else ""
        print(f"  [{icon}] {name} ({latency_ms:.0f}ms){suffix}")
        time.sleep(PACE_SECONDS)
        return result


# ── Individual checks ───────────────────────────────────────────────────

def check_health(v: Verifier) -> str:
    r = v.client.get("/health")
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "healthy":
        raise AssertionError(f"unexpected /health body: {body}")
    return f"environment={body.get('environment')}"


def check_login(v: Verifier) -> str:
    r = v.client.post(f"{V1}/auth/token", json={"email": v.email, "password": v.password})
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise AssertionError("login succeeded but no access_token in response")
    v.token = token
    return "token acquired"


def check_jwks(v: Verifier) -> str:
    r = v.client.get(f"{V1}/counterfactual/jwks")
    r.raise_for_status()
    keys = r.json().get("keys", [])
    if not keys:
        raise AssertionError("jwks response has no keys")
    return f"{len(keys)} key(s)"


def check_financial_audit_demo(v: Verifier) -> str:
    """Self-contained, deterministic, canned-data audit -- no setup needed,
    exercises the whole counterfactual/forensic-audit pillar in one call."""
    r = v.client.get(f"{V1}/counterfactual/audit/financial/demo", headers=v._auth_headers())
    r.raise_for_status()
    body = r.json()
    if "record_hash" not in body and "report" not in body:
        raise AssertionError(f"unexpected demo-audit shape: {list(body.keys())}")
    return "signed demo audit returned"


def check_query_history(v: Verifier) -> str:
    r = v.client.get(f"{V1}/query-history", headers=v._auth_headers())
    r.raise_for_status()
    return f"http {r.status_code}"


def check_saved_queries(v: Verifier) -> str:
    r = v.client.get(f"{V1}/saved-queries", headers=v._auth_headers())
    r.raise_for_status()
    return f"http {r.status_code}"


def check_dashboards_list(v: Verifier) -> str:
    r = v.client.get(f"{V1}/dashboards", headers=v._auth_headers())
    r.raise_for_status()
    return f"http {r.status_code}"


def check_dashboard_create(v: Verifier) -> str:
    name = v.ns("dashboard")
    r = v.client.post(
        f"{V1}/dashboards", headers=v._auth_headers(),
        json={"name": name, "description": "live-verify run, safe to delete", "tiles": []},
    )
    r.raise_for_status()
    body = r.json()
    if not body.get("success"):
        raise AssertionError(f"dashboard create did not report success: {body}")
    return f"created {name}"


def check_chat(v: Verifier) -> str:
    """Ask AURA, minimal scope: confirms the endpoint answers coherently.
    Does NOT exercise a full NL->SQL->chart cycle against an uploaded file
    (that needs a data source in scope, out of this pass) -- reported
    honestly as a partial check, not a full pipeline proof."""
    r = v.client.post(
        f"{V1}/chat", headers=v._auth_headers(),
        json={"message": "What can you help me analyze?", "auto_execute": False},
    )
    r.raise_for_status()
    body = r.json()
    if not body:
        raise AssertionError("chat returned an empty response")
    return "responded (partial check: no data source in scope)"


def check_pipeline_generate(v: Verifier) -> str:
    r = v.client.post(
        f"{V1}/pipeline/generate", headers=v._auth_headers(),
        json={"prompt": "count rows", "include_schema": False},
    )
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "success":
        raise AssertionError(f"pipeline generate did not report success: {body.get('error')}")
    return "pipeline generated"


def check_webhooks(v: Verifier) -> str:
    r = v.client.post(
        f"{V1}/webhooks", headers=v._auth_headers(),
        json={"url": "https://example.com/aura-verify-hook", "events": ["uasr.drift"],
              "description": v.ns("webhook")},
    )
    r.raise_for_status()
    sub = r.json()
    # Response is {"status": "success", "webhook": {"id": ..., ...}} --
    # confirmed live 2026-08-31 (BUG-006 in docs/BUG_REGISTRY.md: an earlier
    # version of this check assumed "id" at the top level and false-
    # positived on a working webhook create).
    sub_id = sub.get("webhook", {}).get("id")
    if not sub_id:
        raise AssertionError(f"webhook create returned no id: {sub}")

    r2 = v.client.get(f"{V1}/webhooks", headers=v._auth_headers())
    r2.raise_for_status()

    r3 = v.client.post(f"{V1}/webhooks/{sub_id}/test", headers=v._auth_headers())
    r3.raise_for_status()
    return f"created + listed + test-fired ({sub_id})"


def check_uasr_self_heal(v: Verifier) -> str:
    """Regression check for the exact bug fixed in fix/uasr-schema-
    validation-false-reject: identical values, only a column renamed --
    must deploy correctly (post_kl: 0.0), same as verified manually
    2026-08-31."""
    source_id = v.ns("uasr")
    rows = [{"user_id": i, "amount": round(10 + i * 0.37, 2), "status": "active"} for i in range(1, 11)]
    r = v.client.post(
        f"{V1}/uasr/baseline", headers=v._auth_headers(),
        json={"source_id": source_id, "rows": rows},
    )
    r.raise_for_status()

    drifted = [{"user_id": i, "total_amount": round(10 + i * 0.37, 2), "status": "active"} for i in range(1, 11)]
    r2 = v.client.post(
        f"{V1}/uasr/ingest", headers=v._auth_headers(),
        json={"source_id": source_id, "rows": drifted},
    )
    r2.raise_for_status()
    body = r2.json()
    if body.get("status") != "deployed":
        raise AssertionError(f"expected auto-heal to deploy, got status={body.get('status')!r}: {body}")
    return f"drift detected + shim deployed (post_kl={body.get('post_kl')})"


CHECKS: List[tuple[str, Callable[[Verifier], Optional[str]]]] = [
    ("health", check_health),
    ("login", check_login),
    ("jwks", check_jwks),
    ("financial_audit_demo", check_financial_audit_demo),
    ("query_history", check_query_history),
    ("saved_queries", check_saved_queries),
    ("dashboards_list", check_dashboards_list),
    ("dashboard_create", check_dashboard_create),
    ("chat", check_chat),
    ("pipeline_generate", check_pipeline_generate),
    ("webhooks", check_webhooks),
    ("uasr_self_heal", check_uasr_self_heal),
]


def write_report(results: List[CheckResult], base_url: str, out_path: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    skipped = sum(1 for r in results if r.status == "skip")
    lines = [
        f"# Live verification run — {now}",
        "",
        f"Target: `{base_url}`",
        f"**{passed} passed, {failed} failed, {skipped} skipped** (of {len(results)})",
        "",
        "| Check | Status | Latency | Detail |",
        "|---|---|---|---|",
    ]
    for r in results:
        detail = r.detail.replace("|", "\\|")[:200]
        lines.append(f"| {r.name} | {r.status} | {r.latency_ms:.0f}ms | {detail} |")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReport written to {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("STAGING_URL", DEFAULT_URL))
    parser.add_argument("--out", default=None, help="Markdown report path (default: timestamped in cwd)")
    args = parser.parse_args()

    email = os.getenv("STAGING_EMAIL")
    password = os.getenv("STAGING_PASSWORD")
    if not email or not password:
        print("STAGING_EMAIL and STAGING_PASSWORD env vars are required.", file=sys.stderr)
        return 2

    print(f"Verifying {args.url} ...\n")
    v = Verifier(base_url=args.url, email=email, password=password)
    for name, fn in CHECKS:
        v.run(name, lambda fn=fn: fn(v))

    passed = sum(1 for r in v.results if r.status == "pass")
    failed = sum(1 for r in v.results if r.status == "fail")
    skipped = sum(1 for r in v.results if r.status == "skip")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped (of {len(v.results)})")

    out_path = args.out or f"live_verify_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
    write_report(v.results, args.url, out_path)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
