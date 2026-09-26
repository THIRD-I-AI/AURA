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
import io
import os
import socket
import ssl
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional
from urllib.parse import urlparse

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


def check_file_upload_profile(v: Verifier) -> str:
    """README row: "File upload -> profile -> query, Gateway-native,
    /data/uploads volume" (deploy/aws-free-tier/README.md). Builds a tiny
    synthetic CSV entirely in-process -- no dependency on any file already
    on the box -- and drives POST /upload (aurabackend/api_gateway/routers/
    files.py:97) then GET /files/{file_id}/profile (files.py:276).

    file_id is the filename itself, not a generated id -- confirmed by the
    profile route's own comment ("file_id is a filename (\"sales.csv\"),
    not an unguessable id") -- so the upload response's `filename` is
    reused directly as the path param.
    """
    filename = f"{v.ns('upload')}.csv"
    csv_body = "id,name,amount\n1,alpha,10.5\n2,beta,20.25\n3,gamma,30.75\n"
    r = v.client.post(
        f"{V1}/upload", headers=v._auth_headers(),
        files={"file": (filename, csv_body.encode("utf-8"), "text/csv")},
    )
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "success":
        raise AssertionError(f"upload did not report success: {body}")
    file_id = body.get("filename") or filename

    # The profile write is intentionally backgrounded (fire_and_forget,
    # non-blocking -- BUG-030's fix) rather than part of the upload
    # response's own request/response cycle, so it can genuinely still be
    # in flight the instant this check asks for it. Poll briefly rather
    # than asserting on the very next request.
    profile = None
    for _ in range(10):
        r2 = v.client.get(f"{V1}/files/{file_id}/profile", headers=v._auth_headers())
        if r2.status_code == 200 and r2.json().get("status") == "success":
            profile = r2.json()
            break
        time.sleep(0.5)
    if profile is None:
        raise AssertionError(f"profile never became available after 5s: last response {r2.status_code} {r2.text[:200]}")
    return (
        f"uploaded {file_id} ({body.get('bytes')}B) + profiled "
        f"({profile.get('columns_count')} cols, {profile.get('rows_count')} rows)"
    )


def check_ledger_proof(v: Verifier) -> str:
    """README row: "External verification: /jwks, signed tree head, Merkle
    proofs, Gateway-native" (deploy/aws-free-tier/README.md). Exercises the
    durable, tenant-scoped audit ledger (Subsystem C) end to end: run a small
    signed financial audit under our own tenant (POST /counterfactual/audit/
    financial, aurabackend/counterfactual_service/main.py:678-694, proxied at
    aurabackend/api_gateway/routers/counterfactual.py:181), then prove it via
    the chain-verify and RFC 6962 Merkle inclusion-proof reads (GET
    /counterfactual/audit/ledger/verify and GET /counterfactual/audit/ledger/
    proof/{cert_hash}, counterfactual.py:202-218).

    Deliberately does NOT reuse /counterfactual/audit/financial/demo for the
    write half: that route always signs anonymously (user=None) and its
    ledger entry permanently chains under the "default" tenant (see
    forensic_demo.py's dataset and main.py:754's own comment), which would
    almost never match our authenticated caller's JWT tenant on
    /audit/ledger/proof (tenant is taken from require_tenant, never a body
    field) -- so that combination would 404 by construction, not because
    anything is broken.

    Also deliberately does NOT target GET /counterfactual/audit/sth + GET
    /counterfactual/audit/inclusion/{record_hash} (the OTHER Merkle surface,
    Sprint 19 TRAIGA's literal "Signed Tree Head", main.py:1067-1152): no
    endpoint anywhere returns the chain's own record_hash for a given
    action, only the unrelated inner report hash -- so there is no way for
    an HTTP-only client to discover a real record_hash to fetch an inclusion
    proof for. The ledger pair above is the one "signed + Merkle-provable"
    path that is actually drivable end-to-end from outside the box.
    """
    r = v.client.post(
        f"{V1}/counterfactual/audit/financial", headers=v._auth_headers(),
        json={
            "tenant_id": "ignored-tenant-comes-from-jwt",
            "subject_id": v.ns("ledger"),
            "preparer_id": "live-verify",
            "ledger": [{"internal_id": "verify-L1", "account_code": "4000", "amount": 100.0}],
        },
    )
    r.raise_for_status()
    body = r.json()
    cert_hash = body.get("record_hash")
    if not cert_hash:
        raise AssertionError(f"financial audit did not return record_hash: {list(body.keys())}")

    r2 = v.client.get(f"{V1}/counterfactual/audit/ledger/verify", headers=v._auth_headers())
    r2.raise_for_status()
    verify_body = r2.json()
    if not verify_body.get("ok"):
        raise AssertionError(f"ledger chain failed verification: {verify_body}")

    r3 = v.client.get(f"{V1}/counterfactual/audit/ledger/proof/{cert_hash}", headers=v._auth_headers())
    r3.raise_for_status()
    proof = r3.json()
    if "root_hash_hex" not in proof or "proof_hex" not in proof:
        raise AssertionError(f"unexpected inclusion-proof shape: {list(proof.keys())}")
    return (
        f"signed audit {cert_hash[:12]}... chained + verified "
        f"(tree_size={proof.get('tree_size')}, leaf_index={proof.get('leaf_index')})"
    )


def check_uasr_self_heal(v: Verifier) -> str:
    """Regression check for the exact bug fixed in fix/uasr-schema-
    validation-false-reject: identical values, only a column renamed --
    must be detected and healed, same as verified manually 2026-08-31.

    DSR-015 (2026-09-14) flipped UASR_RISK_TIERED's default to true, so a
    validated shim is now held `pending_approval` rather than
    auto-deployed (default-safe: human approval, not the old default-
    unsafe auto-deploy). This drives the full path -- ingest -> pending
    approval -> POST /uasr/recovery/{id}/approve -> deployed -- so the
    check still proves the feature actually heals, not just that it
    pauses (see docs/BUG_REGISTRY.md BUG-049)."""
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
    status = body.get("status")

    if status == "deployed":
        return f"drift detected + shim auto-deployed (post_kl={body.get('post_kl')})"

    if status != "pending_approval":
        raise AssertionError(f"expected deployed or pending_approval, got status={status!r}: {body}")

    recovery_id = body.get("recovery_id")
    if not recovery_id:
        raise AssertionError(f"pending_approval response has no recovery_id: {body}")

    r3 = v.client.post(
        f"{V1}/uasr/recovery/{recovery_id}/approve", headers=v._auth_headers(),
        json={"approver": "verify_live_deployment"},
    )
    r3.raise_for_status()
    approved = r3.json()
    recovery_status = approved.get("recovery", {}).get("status")
    if recovery_status != "deployed":
        raise AssertionError(f"expected approval to deploy the shim, got status={recovery_status!r}: {approved}")
    return f"drift detected -> held for approval -> approved -> deployed (recovery_id={recovery_id})"


# ── Fix-verification probes ─────────────────────────────────────────────
#
# The checks above prove the product's features work. These prove that specific
# bug FIXES are live on the deployed system: each one drives the real endpoint
# the bug lived behind and fails if the OLD behaviour is still there. A FAIL means
# "the fix is not deployed, or it regressed" -- passing unit tests and a green CI
# say nothing about what the box is actually running (its /health reports only
# version 2.0.0, no build id), so this is the only direct evidence.
#
# Every probe is non-destructive (rejected requests, or resources namespaced
# verify_* and deleted before the check returns).

def _minimal_xlsx(header: List[str], rows: List[List[object]]) -> bytes:
    """A real .xlsx built with the stdlib only (inline strings), so the script keeps needing just httpx."""
    def cell(ref: str, val: object) -> str:
        if isinstance(val, (int, float)):
            return f'<c r="{ref}"><v>{val}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{val}</t></is></c>'

    all_rows = [header] + rows
    sheet_rows = ""
    for ri, row in enumerate(all_rows, start=1):
        cells = "".join(cell(f"{chr(65 + ci)}{ri}", val) for ci, val in enumerate(row))
        sheet_rows += f'<row r="{ri}">{cells}</row>'
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            '<sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{sheet_rows}</sheetData></worksheet>"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def check_fix_bug196_dashboard_cannot_read_files(v: Verifier) -> str:
    """BUG-196: a saved query on a dashboard tile could read the server's filesystem.
    glob('/etc/*') is the probe because it is decisive whether or not any particular file exists:
    on the old code the tile answers 'success'; on the fixed code DuckDB refuses (file access is off)."""
    h = v._auth_headers()
    sq_id = dash_id = None
    try:
        r = v.client.post(f"{V1}/saved-queries", headers=h, json={"name": v.ns("fs_probe"), "sql": "SELECT COUNT(*) AS n FROM glob('/etc/*')"})
        r.raise_for_status()
        sq_id = r.json().get("id") or r.json().get("query", {}).get("id")
        if not sq_id:
            raise AssertionError(f"could not read the saved query id from {r.text[:200]}")
        r = v.client.post(f"{V1}/dashboards", headers=h, json={"name": v.ns("fs_probe_dash"), "tiles": [{"saved_query_id": sq_id}]})
        r.raise_for_status()
        dash_id = r.json().get("id") or r.json().get("dashboard", {}).get("id")
        if not dash_id:
            raise AssertionError(f"could not read the dashboard id from {r.text[:200]}")
        r = v.client.post(f"{V1}/dashboards/{dash_id}/render", headers=h)
        r.raise_for_status()
        tiles = r.json().get("tiles", [])
        if not tiles:
            raise AssertionError("render returned no tiles")
        if tiles[0].get("status") == "success":
            raise AssertionError("BUG-196 NOT FIXED: a dashboard tile listed the server filesystem (glob('/etc/*') succeeded)")
        return f"tile refused the filesystem probe (status={tiles[0].get('status')})"
    finally:
        if dash_id:
            v.client.delete(f"{V1}/dashboards/{dash_id}", headers=h)
        if sq_id:
            v.client.delete(f"{V1}/saved-queries/{sq_id}", headers=h)


def check_fix_bug179_oversized_upload_rejected_early(v: Verifier) -> str:
    """BUG-179: the 25MB limit only fired after the whole body was spooled. Declares a 200MB body and
    sends none: a fixed gateway answers 413 from the header alone; the old one waits for a body that
    never arrives. (A reverse proxy with its own body limit could also produce the 413 -- see detail.)"""
    u = urlparse(v.base_url)
    host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    raw = socket.create_connection((host, port), timeout=10)
    if u.scheme == "https":
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # never negotiate below TLS 1.2 (CodeQL: insecure protocol)
        sock = ctx.wrap_socket(raw, server_hostname=host)
    else:
        sock = raw
    try:
        sock.settimeout(10)
        req = (
            f"POST {V1}/upload HTTP/1.1\r\nHost: {host}\r\nAuthorization: Bearer {v.token}\r\n"
            "Content-Type: multipart/form-data; boundary=verify\r\nContent-Length: 209715200\r\nConnection: close\r\n\r\n"
        )
        sock.sendall(req.encode())
        try:
            status_line = sock.recv(200).split(b"\r\n", 1)[0].decode(errors="replace")
        except socket.timeout:
            raise AssertionError("BUG-179 NOT FIXED: no response within 10s to a 200MB Content-Length (the server is waiting for the body)")
    finally:
        sock.close()
    if " 413 " not in status_line:
        raise AssertionError(f"expected 413 for a 200MB declared body, got: {status_line}")
    return "413 returned from the declared length alone (may come from the app or a proxy in front of it)"


def check_fix_bug188_preview_limit_validated(v: Verifier) -> str:
    """BUG-188: ETL preview's `limit` was spliced into SQL unvalidated. The fixed handler validates it
    BEFORE it looks for the file, so a non-integer limit is a 400; the old handler got as far as 404."""
    r = v.client.post(f"{V1}/etl/preview-source", headers=v._auth_headers(), json={"source_file": "verify_missing.csv", "limit": "abc"})
    if r.status_code != 400:
        raise AssertionError(f"BUG-188 NOT FIXED: a non-integer limit returned {r.status_code}, expected 400")
    return "non-integer limit rejected with 400"


def check_fix_bug174_170_connection_settings(v: Verifier) -> str:
    """BUG-174 + BUG-170: pathological credentials get a clean 400 (was a 500 RecursionError), and a
    valid BigQuery connection persists its encrypted settings (proves the config_encrypted migration ran)."""
    h = v._auth_headers()
    r = v.client.post(f"{V1}/connections", headers=h, json={
        "name": v.ns("bq_bad"), "type": "bigquery", "extra": {"project_id": "p", "credentials_json": "[" * 5000}})
    if r.status_code == 400 and "unavailable" in r.text.lower():
        raise SkipCheck("bigquery connector is not available on this box")
    if r.status_code != 400 or "JSON object" not in r.text:
        # An old gateway ACCEPTS this request and creates a connection: remove it before failing.
        if r.status_code == 200:
            stray = r.json().get("connection", {}).get("id")
            if stray:
                v.client.delete(f"{V1}/connections/{stray}", headers=h)
        raise AssertionError(f"BUG-174/170 NOT LIVE: nested credentials returned {r.status_code}: {r.text[:160]}")
    conn_id = None
    try:
        r = v.client.post(f"{V1}/connections", headers=h, json={
            "name": v.ns("bq_ok"), "type": "bigquery",
            "extra": {"project_id": "verify-proj", "dataset": "verify_ds", "credentials_json": '{"type": "service_account"}'}})
        if r.status_code != 200:
            raise AssertionError(f"BUG-170 NOT LIVE: a valid BigQuery connection returned {r.status_code}: {r.text[:160]}")
        conn = r.json().get("connection", {})
        conn_id = conn.get("id")
        if "credentials_json" in r.text or "service_account" in r.text:
            raise AssertionError("BUG-170 REGRESSION: the response echoed the stored credentials")
        return "bad credentials -> 400; valid settings stored and not echoed"
    finally:
        if conn_id:
            v.client.delete(f"{V1}/connections/{conn_id}", headers=h)


def check_fix_bug146_xlsx_upload_profiles(v: Verifier) -> str:
    """BUG-146/176/180: an uploaded .xlsx workbook must load and profile (it was stored, then ignored)."""
    filename = f"{v.ns('xlsx')}.xlsx"
    body = _minimal_xlsx(["id", "name", "amount"], [[1, "alpha", 10.5], [2, "beta", 20.25], [3, "gamma", 30.75]])
    r = v.client.post(f"{V1}/upload", headers=v._auth_headers(), files={"file": (filename, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    r.raise_for_status()
    file_id = r.json().get("filename") or filename
    profile = None
    for _ in range(12):
        r2 = v.client.get(f"{V1}/files/{file_id}/profile", headers=v._auth_headers())
        if r2.status_code == 200 and r2.json().get("status") == "success":
            profile = r2.json()
            break
        time.sleep(0.5)
    if profile is None:
        raise AssertionError(f"BUG-146 NOT FIXED: the .xlsx never produced a profile (last: {r2.status_code} {r2.text[:160]})")
    if profile.get("columns_count") != 3 or profile.get("rows_count") != 3:
        raise AssertionError(f"xlsx profiled wrongly: {profile.get('columns_count')} cols / {profile.get('rows_count')} rows, expected 3/3")
    return f"{file_id} profiled: 3 cols, 3 rows"


FIX_CHECKS: List[tuple[str, Callable[[Verifier], Optional[str]]]] = [
    ("fix:bug196_dashboard_fs_locked", check_fix_bug196_dashboard_cannot_read_files),
    ("fix:bug179_oversized_upload_413", check_fix_bug179_oversized_upload_rejected_early),
    ("fix:bug188_preview_limit_400", check_fix_bug188_preview_limit_validated),
    ("fix:bug174_170_connection_settings", check_fix_bug174_170_connection_settings),
    ("fix:bug146_xlsx_profiles", check_fix_bug146_xlsx_upload_profiles),
]


CHECKS: List[tuple[str, Callable[[Verifier], Optional[str]]]] = [
    ("health", check_health),
    ("login", check_login),
    ("jwks", check_jwks),
    ("financial_audit_demo", check_financial_audit_demo),
    ("file_upload_profile", check_file_upload_profile),
    ("ledger_proof", check_ledger_proof),
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


def append_ledger(results: List[CheckResult], base_url: str, path: str) -> None:
    """One row per run in a committed ledger, so the deployed state over time is a tracked fact."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    features = [r for r in results if not r.name.startswith("fix:")]
    fixes = [r for r in results if r.name.startswith("fix:")]
    feat_ok = sum(1 for r in features if r.status == "pass")
    live = [r.name[4:] for r in fixes if r.status == "pass"]
    missing = [r.name[4:] for r in fixes if r.status == "fail"]
    skipped = [r.name[4:] for r in fixes if r.status == "skip"]
    row = (f"| {now} | {feat_ok}/{len(features)} | {len(live)}/{len(fixes)} | "
           f"{', '.join(live) or '-'} | {', '.join(missing) or '-'} | {', '.join(skipped) or '-'} |")
    header_lines = [
        "# Live deployment ledger",
        "",
        f"Target: `{base_url}`. One row per `scripts/verify_live_deployment.py --append-log` run.",
        "`Fixes live` counts the `fix:` probes that PASS against the deployed system; `NOT live` are merged bug",
        "fixes the deployed system does not yet exhibit (not deployed, or regressed).",
        "",
        "| Run (UTC) | Features | Fixes live | Live | NOT live | Skipped |",
        "|---|---|---|---|---|---|",
    ]
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    if not existing.strip():
        existing = "\n".join(header_lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(existing.rstrip("\n") + "\n" + row + "\n")
    print(f"Ledger row appended to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("STAGING_URL", DEFAULT_URL))
    parser.add_argument("--out", default=None, help="Markdown report path (default: timestamped in cwd)")
    parser.add_argument("--append-log", default=None, metavar="PATH",
                        help="Append one dated summary row (incl. which fixes are live) to this ledger file")
    args = parser.parse_args()

    email = os.getenv("STAGING_EMAIL")
    password = os.getenv("STAGING_PASSWORD")
    if not email or not password:
        print("STAGING_EMAIL and STAGING_PASSWORD env vars are required.", file=sys.stderr)
        return 2

    print(f"Verifying {args.url} ...\n")
    v = Verifier(base_url=args.url, email=email, password=password)
    for name, fn in CHECKS + FIX_CHECKS:
        v.run(name, lambda fn=fn: fn(v))

    passed = sum(1 for r in v.results if r.status == "pass")
    failed = sum(1 for r in v.results if r.status == "fail")
    skipped = sum(1 for r in v.results if r.status == "skip")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped (of {len(v.results)})")

    out_path = args.out or f"live_verify_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
    write_report(v.results, args.url, out_path)
    if args.append_log:
        append_ledger(v.results, args.url, args.append_log)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
