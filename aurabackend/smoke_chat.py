"""Smoke test the full chat path with real data.

Boots the api_gateway in-process via FastAPI's TestClient (no Docker),
hits POST /api/v1/chat with a real question about an existing upload,
and prints what happens. This is the empirical baseline for "does AURA
actually work end-to-end?"

Usage:
    cd aurabackend
    python smoke_chat.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Suppress noisy import warnings
os.environ.setdefault("PYTHONWARNINGS", "ignore::DeprecationWarning")

from fastapi.testclient import TestClient

from api_gateway.main import app

client = TestClient(app)


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main() -> int:
    section("1. Health check")
    r = client.get("/health")
    print(f"  /health -> {r.status_code} {r.json()}")

    section("2. List discovered files")
    r = client.get("/api/v1/files")
    if r.status_code == 200:
        files = r.json().get("files", [])
        print(f"  found {len(files)} file(s)")
        for f in files[:5]:
            print(f"    - {f.get('filename')}")
    else:
        print(f"  /files -> {r.status_code}: {r.text[:200]}")

    section("3. Chat: ask a question about an existing CSV")
    questions = [
        "list the first 5 rows of customer",
        "how many rows are in product",
    ]
    for q in questions:
        print(f"\n  Q: {q!r}")
        t0 = time.perf_counter()
        r = client.post(
            "/api/v1/chat",
            json={
                "message": q,
                "session_id": f"smoke_{int(t0*1000)}",
                "auto_execute": True,
            },
        )
        dt = (time.perf_counter() - t0) * 1000
        print(f"  status: {r.status_code} ({dt:.0f}ms)")
        if r.status_code != 200:
            print(f"  body: {r.text[:500]}")
            continue
        body = r.json()
        print(f"  result status: {body.get('status')}")
        print(f"  available tables: {body.get('available_tables', [])}")
        if body.get("error_message"):
            print(f"  ERROR: {body['error_message']}")
        if body.get("final_query"):
            print(f"  SQL: {body['final_query'][:200]}")
        er = body.get("execution_result") or {}
        if er.get("success"):
            print(f"  rows: {er.get('row_count', 0)}, cols: {er.get('columns', [])[:5]}")
            preview = er.get("rows", [])[:2]
            for row in preview:
                print(f"    {row}")
        elif er.get("error"):
            print(f"  EXEC ERROR: {er['error']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
