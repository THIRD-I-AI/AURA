#!/usr/bin/env python3
"""Restore-drill checks. Prints counts and names only: never a secret or a customer value.

  data       in the restored api_gateway container: integrity and row counts per database, file counts,
             saved-credential decrypt counts, the audit-ledger chain per tenant, a signing-key round trip
  health     in the container: one /health probe; exit 0 only for HTTP 200 and status "healthy"
  isolation  in the container: exit 3 if the app can reach the internet
  register   in the container: create a throwaway account for scripts/verify_live_deployment.py
  live       on the drill host: judge that verifier's output (stdin) against EXPECTED_OFFLINE

`data` and `live` end with the lines restore-drill-onbox.sh folds into its verdict:
  DRILL_CHECK <name> <PASS|FAIL> <detail>   gating
  DRILL_NOTE <text>                         reported, never gating
and exit 1 if any DRILL_CHECK failed. `live` runs on the host's Python 3.9: no newer syntax here.
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Live-check failures that are expected on a restored copy with no route out, each with its reason.
# Any other failing check fails the drill: the exemption covers these four and nothing else.
EXPECTED_OFFLINE = {
    "chat": "it calls an LLM provider",
    "pipeline_generate": "it calls an LLM provider",
    "webhooks": "its test delivery posts to an external URL",
    "backup_fresh": "backup_status.json is written by backup.sh on the live box and is not part of a backup",
}

# What backup.sh archives into files.tar.gz.
FILE_DIRS = ("keys", "uploads", "audit", "artifacts", "uasr_parquet")

_RESULT_LINE = re.compile(r"^\s*\[(PASS|FAIL|SKIP)\]\s+(\S+)")
_SUMMARY_LINE = re.compile(r"(\d+) passed, (\d+) failed, (\d+) skipped \(of (\d+)\)")
# Anything key-shaped in an error message is masked before it can reach the report.
_KEY_SHAPED = re.compile(r"[A-Za-z0-9+/_=-]{32,}")


def _safe(exc):
    return type(exc).__name__ + ": " + _KEY_SHAPED.sub("<redacted>", str(exc))[:160]


def _read_only_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


# ── collection ──────────────────────────────────────────────────────────────


def check_databases(state_dir):
    """Integrity and size of every SQLite database in state_dir, opened read-only."""
    found = {}
    for path in sorted(glob.glob(os.path.join(state_dir, "*.db"))):
        name = os.path.basename(path)
        try:
            conn = sqlite3.connect(_read_only_uri(path), uri=True)
            try:
                tables = [t for (t,) in conn.execute("select name from sqlite_master where type='table'")]
                rows = 0
                for table in tables:
                    try:
                        rows += conn.execute('select count(*) from "%s"' % table.replace('"', '""')).fetchone()[0]
                    except sqlite3.Error:
                        pass  # a virtual table whose module is not loaded cannot be counted; integrity still covers it
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                found[name] = {"integrity": str(integrity)[:120], "tables": len(tables), "rows": rows}
            finally:
                conn.close()
        except sqlite3.Error as exc:
            found[name] = {"integrity": "error: " + type(exc).__name__}
    return found


def count_files(data_dir):
    return {
        d: sum(len(files) for _, _, files in os.walk(os.path.join(data_dir, d)))
        for d in FILE_DIRS
        if os.path.isdir(os.path.join(data_dir, d))
    }


def check_saved_credentials(db_path):
    """How many saved connection secrets decrypt with the restored environment's key."""
    from shared.credentials import decrypt_secret  # only importable inside the gateway image

    decrypted = failed = 0
    conn = sqlite3.connect(_read_only_uri(db_path), uri=True)
    try:
        for row in conn.execute("select password_encrypted, config_encrypted from gateway_connections"):
            for token in row:
                if token:
                    try:
                        decrypt_secret(token)
                        decrypted += 1
                    except Exception:
                        failed += 1
    finally:
        conn.close()
    return {"decrypted": decrypted, "failed": failed}


async def check_ledger():
    """Verify the hash chain of every tenant in the restored audit ledger."""
    from sqlalchemy import select

    from shared import audit_ledger as al

    async with al.session_scope() as session:
        tenants = [t for (t,) in (await session.execute(select(al.AuditLedgerRow.tenant_id).distinct())).all()]
    results = [await al.verify_chain(t) for t in tenants]
    return {
        "tenants": len(results),
        "records": sum(r["count"] for r in results),
        "chains_ok": sum(1 for r in results if r["ok"]),
        "failures": sum(len(r["failures"]) for r in results),
    }


def check_signing_key(path):
    from cryptography.hazmat.primitives import serialization

    with open(path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    signature = key.sign(b"restore-drill")
    key.public_key().verify(signature, b"restore-drill")
    return "loads and signs"


def collect(data_dir="/data"):
    state = os.path.join(data_dir, "state")
    report = {"databases": check_databases(state), "files": count_files(data_dir)}
    try:
        report["saved_credentials"] = check_saved_credentials(os.path.join(state, "gateway.db"))
    except Exception as exc:
        report["saved_credentials_error"] = _safe(exc)
    try:
        report["audit_ledger"] = asyncio.run(check_ledger())
    except Exception as exc:
        report["audit_ledger_error"] = _safe(exc)
    key = os.path.join(data_dir, "keys", "signing_ed25519.pem")
    if os.path.exists(key):
        try:
            report["signing_key"] = check_signing_key(key)
        except Exception as exc:
            report["signing_key"] = "error: " + type(exc).__name__
    else:
        report["signing_key"] = "absent"
    return report


# ── verdicts ────────────────────────────────────────────────────────────────


def judge_data(report, expected_dbs=()):
    """(checks, notes) for a collected report. Gating: db_integrity and ledger_chains."""
    checks, notes = [], []

    dbs = report.get("databases", {})
    bad = sorted(name for name, d in dbs.items() if d.get("integrity") != "ok")
    missing = sorted(set(expected_dbs) - set(dbs))
    if not dbs:
        checks.append(("db_integrity", "FAIL", "no SQLite databases were found under /data/state"))
    elif bad or missing:
        parts = []
        if bad:
            parts.append("integrity_check failed: " + ", ".join(bad))
        if missing:
            parts.append("in the backup but not restored: " + ", ".join(missing))
        checks.append(("db_integrity", "FAIL", "; ".join(parts)))
    else:
        checks.append(("db_integrity", "PASS", "%d databases ok (%s)" % (len(dbs), ", ".join(sorted(dbs)))))

    ledger = report.get("audit_ledger")
    if ledger is None:
        reason = report.get("audit_ledger_error", "no result")
        checks.append(("ledger_chains", "FAIL", "the audit ledger could not be verified: " + reason))
    elif ledger["failures"] or ledger["chains_ok"] != ledger["tenants"]:
        broken = ledger["tenants"] - ledger["chains_ok"]
        checks.append((
            "ledger_chains",
            "FAIL",
            "%d of %d chains failed verification (%d bad records)" % (broken, ledger["tenants"], ledger["failures"]),
        ))
    else:
        checks.append((
            "ledger_chains",
            "PASS",
            "%d chains, %d records, 0 failures" % (ledger["tenants"], ledger["records"]),
        ))
        if ledger["tenants"] == 0:
            notes.append("ledger: the backup holds no audit-ledger chains, so nothing was verified")

    creds = report.get("saved_credentials")
    if creds is None:
        notes.append("saved_credentials: not checked (%s)" % report.get("saved_credentials_error", "no result"))
    elif creds["failed"]:
        notes.append(
            "WARN saved_credentials: %d of %d did not decrypt with the restored environment's key"
            % (creds["failed"], creds["failed"] + creds["decrypted"])
        )
    else:
        notes.append("saved_credentials: %d decrypted, 0 failed" % creds["decrypted"])

    signing = report.get("signing_key", "absent")
    notes.append(("WARN " if signing.startswith("error") else "") + "signing_key: " + signing)
    notes.append("files: " + (", ".join("%s=%d" % kv for kv in sorted(report.get("files", {}).items())) or "none"))
    return checks, notes


def judge_live(text):
    """(checks, notes) for the output of scripts/verify_live_deployment.py."""
    results = []
    for line in text.splitlines():
        match = _RESULT_LINE.match(line)
        if match:
            results.append((match.group(2), match.group(1)))
    summary = _SUMMARY_LINE.search(text)
    if not results or not summary or int(summary.group(4)) != len(results):
        return [("live_checks", "FAIL", "the live verifier did not run to completion")], []

    failed = [name for name, status in results if status == "FAIL"]
    skipped = [name for name, status in results if status == "SKIP"]
    expected = [name for name in failed if name in EXPECTED_OFFLINE]
    unexpected = [name for name in failed if name not in EXPECTED_OFFLINE]
    detail = "%d/%d pass" % (len(results) - len(failed) - len(skipped), len(results))
    if expected:
        detail += "; expected offline: " + ", ".join(expected)
    if skipped:
        detail += "; skipped: " + ", ".join(skipped)
    notes = ["live check %s failed offline, as expected: %s" % (name, EXPECTED_OFFLINE[name]) for name in expected]
    if unexpected:
        return [("live_checks", "FAIL", "unexpected failures: " + ", ".join(unexpected) + "; " + detail)], notes
    return [("live_checks", "PASS", detail)], notes


# ── probes that run inside the app container ────────────────────────────────


def probe_health(url="http://localhost:8000/health"):
    """(http status, /health status, one summary line). A 503 is read too: it names the failing probe."""
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            code, body = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        code, body = exc.code, exc.read()
    try:
        data = json.loads(body)
    except ValueError:
        data = {}
    summary = "%s %s build=%s ops=%s checks=%s" % (
        code, data.get("status"), data.get("build"), json.dumps(data.get("ops")), json.dumps(data.get("checks")),
    )
    return code, data.get("status"), summary


def probe_isolation():
    """0 if the app has no route out, 3 if any probe got through. The IP probe does not depend on DNS."""
    probes = (
        ("https to api.github.com", lambda: urllib.request.urlopen("https://api.github.com", timeout=5)),
        ("tcp to 1.1.1.1:443", lambda: socket.create_connection(("1.1.1.1", 443), timeout=5)),
    )
    for label, probe in probes:
        try:
            probe()
        except Exception as exc:
            print("no route out (as intended): %s -> %s" % (label, type(exc).__name__))
        else:
            print("REACHABLE -- isolation FAILED: " + label)
            return 3
    return 0


def register_account(base="http://localhost:8000", creds_path="/tmp/drill_creds"):
    """Create a throwaway account; its credentials stay in a 0600 file inside the container."""
    email = "verify_%s@example.com" % secrets.token_hex(4)
    password = "Vf!" + secrets.token_urlsafe(18)
    request = urllib.request.Request(
        base + "/api/v1/auth/register",
        data=json.dumps({"email": email, "password": password, "name": "Drill"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=20).read()
    fd = os.open(creds_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(email + "\n" + password + "\n")


# ── entry point ─────────────────────────────────────────────────────────────


def emit(checks, notes):
    for name, status, detail in checks:
        print("DRILL_CHECK %s %s %s" % (name, status, " ".join(detail.split())))
    for note in notes:
        print("DRILL_NOTE " + " ".join(note.split()))
    return 1 if any(status != "PASS" for _, status, _ in checks) else 0


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "data":
        # `python3 /tmp/file.py` puts /tmp on sys.path, not the app's directory that holds `shared`.
        sys.path.insert(0, os.getcwd())
        report = collect()
        print(json.dumps(report, indent=1, sort_keys=True))
        return emit(*judge_data(report, os.environ.get("DRILL_EXPECT_DBS", "").split()))
    if mode == "live":
        return emit(*judge_live(sys.stdin.read()))
    if mode == "health":
        try:
            code, status, summary = probe_health()
        except OSError:
            return 1  # not listening yet
        print(summary)
        return 0 if code == 200 and status == "healthy" else 1
    if mode == "isolation":
        return probe_isolation()
    if mode == "register":
        try:
            register_account()
        except Exception as exc:
            print("register failed: " + type(exc).__name__ + " " + str(getattr(exc, "code", "")))
            return 1
        print("drill account registered")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
