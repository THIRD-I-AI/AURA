"""
S43 live smoke test — run against the RUNNING prod compose (Postgres-backed),
NOT part of the default pytest suite (it drives the live stack over HTTP and
needs real LLM keys for the chat step).

Bring the stack up first, then run:

    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
    # wait until the gateway is healthy, then:
    AURA_BASE=http://localhost:8000 python aurabackend/tests/smoke_postgres.py

Proves, on Postgres: register/login (password mode, JWT), upload into the
caller's per-tenant dir (S42) on the durable volume, and chat -> SQL over that
upload. Exit 0 + "SMOKE OK" on success; non-zero + "SMOKE FAILED: ..." on any
failure. The chat step requires GEMINI_API_KEY/GROQ_API_KEY set in the stack's
.env; pass --skip-chat to validate just auth + upload (no LLM key needed).
"""
import io
import os
import sys

import requests

BASE = os.getenv("AURA_BASE", "http://localhost:8000")
V1 = f"{BASE}/api/v1"
SKIP_CHAT = "--skip-chat" in sys.argv


def main() -> None:
    email = f"smoke_{os.urandom(4).hex()}@aura.test"
    pw = "supersafe123"

    # 1. register + login (password mode → a real JWT, the gate S42 isolation needs)
    requests.post(
        f"{V1}/auth/register",
        json={"name": "Smoke", "email": email, "password": pw},
        timeout=30,
    ).raise_for_status()
    tok = requests.post(
        f"{V1}/auth/token", data={"username": email, "password": pw}, timeout=30
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}

    # 2. upload a CSV → lands in this tenant's dir on the durable volume; metadata in Postgres
    csv = b"region,revenue\nNorth,100\nSouth,200\n"
    requests.post(
        f"{V1}/files/upload",
        files={"file": ("smoke.csv", io.BytesIO(csv), "text/csv")},
        headers=headers,
        timeout=60,
    ).raise_for_status()
    listed = requests.get(f"{V1}/files", headers=headers, timeout=30).json()
    assert any(f["filename"] == "smoke.csv" for f in listed.get("files", [])), (
        f"upload not listed back: {listed}"
    )

    if SKIP_CHAT:
        print("SMOKE OK (auth + upload on Postgres; chat skipped via --skip-chat)")
        return

    # 3. chat → SQL over that upload (needs a real LLM key in the stack)
    chat = requests.post(
        f"{V1}/chat",
        json={"message": "total revenue by region", "session_id": "smoke"},
        headers=headers,
        timeout=120,
    ).json()
    assert chat.get("status") == "Success", f"chat failed: {chat.get('error_message')}"
    print("SMOKE OK: register/login + upload + chat all green on Postgres")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — smoke script: any failure is a fail
        print(f"SMOKE FAILED: {exc}")
        sys.exit(1)
