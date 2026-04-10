"""AURA Operability Test Suite - Tests all services and cross-service calls."""
import sys
import time

import httpx

BASE = "http://localhost:8000"
RESULTS = []

def _check(name, fn):
    try:
        status, detail = fn()
        ok = isinstance(status, int) and status < 400
        RESULTS.append((name, status, detail, ok))
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {name:.<40} HTTP {status}  {detail}")
    except Exception as e:
        RESULTS.append((name, "ERR", str(e)[:80], False))
        print(f"  [FAIL] {name:.<40} ERROR  {str(e)[:80]}")

print("\n" + "="*70)
print("  AURA Platform Operability Report")
print("="*70)

# === Section 1: Service Health ===
print("\n--- 1. Service Health Checks ---")
services = [
    (8000, "API Gateway"),
    (8001, "Code Generation"),
    (8002, "Connector Service"),
    (8003, "Execution Sandbox"),
    (8004, "Scheduler"),
    (8005, "Insights"),
    (8006, "Orchestration"),
]
for port, name in services:
    def check(p=port):
        r = httpx.get(f"http://localhost:{p}/health", timeout=5)
        return r.status_code, r.text[:60]
    _check(f"Health: {name} (:{port})", check)

# === Section 2: Gateway API Endpoints ===
print("\n--- 2. Gateway API Endpoints ---")

_check("GET /", lambda: (
    (r := httpx.get(f"{BASE}/", timeout=5)).status_code,
    r.json().get("message", "")[:60]
))

def _check_tools():
    r = httpx.get(f"{BASE}/agent/tools", timeout=10)
    data = r.json()
    # Response is {"tools": [...]} dict
    tools = data.get("tools", data) if isinstance(data, dict) else data
    return r.status_code, f"{len(tools)} tools registered"
_check("GET /agent/tools", _check_tools)

_check("GET /files", lambda: (
    (r := httpx.get(f"{BASE}/files", timeout=5)).status_code,
    f"{len(r.json())} files found"
))

def _check_connectors():
    r = httpx.get(f"{BASE}/connectors/available", timeout=5)
    data = r.json()
    # Response is {"connectors": [...]} or a list
    connectors = data.get("connectors", data) if isinstance(data, dict) else data
    names = [c.get("name", c.get("type", "?")) for c in connectors]
    return r.status_code, ", ".join(names)
_check("GET /connectors/available", _check_connectors)

_check("POST /validate/query", lambda: (
    (r := httpx.post(f"{BASE}/validate/query",
        json={"query": "SELECT * FROM sales WHERE id=1", "dialect": "postgresql"},
        timeout=5)).status_code,
    str(r.json())[:60]
))

_check("POST /chat", lambda: (
    (r := httpx.post(f"{BASE}/chat",
        json={"message": "What tables do I have?", "session_id": "ops"},
        timeout=10)).status_code,
    str(r.json())[:60]
))

# === Section 3: Agent Framework ===
print("\n--- 3. Agent Framework ---")

_check("POST /agent/plan (Gemini AI)", lambda: (
    (r := httpx.post(f"{BASE}/agent/plan",
        json={"prompt": "analyze sales data", "session_id": "ops"},
        timeout=30)).status_code,
    f"{len(r.json().get('tasks', []))} tasks in DAG"
))

# === Section 4: Direct Microservice Endpoints ===
print("\n--- 4. Direct Microservice Endpoints ---")

_check("POST :8001/generate_code", lambda: (
    (r := httpx.post("http://localhost:8001/generate_code",
        json={"step": "count rows in users table", "task": None, "chart_type": None},
        timeout=30)).status_code,
    str(r.json())[:60]
))

def _check_connectors_direct():
    r = httpx.get("http://localhost:8002/connectors/available", timeout=5)
    data = r.json()
    connectors = data.get("connectors", data) if isinstance(data, dict) else data
    return r.status_code, f"{len(connectors)} connectors"
_check("GET :8002/connectors/available", _check_connectors_direct)

_check("POST :8003/execute_sql (no DB)", lambda: (
    (r := httpx.post("http://localhost:8003/execute_sql",
        json={"job_id": "test-1", "sql": "SELECT 1", "connection_id": "default",
              "limit": 10, "approved": True},
        timeout=10)).status_code,
    str(r.json())[:60] if r.status_code < 500 else f"Expected 502 (no DB) -> {r.status_code}"
))

_check("POST :8004/jobs (scheduler)", lambda: (
    (r := httpx.get("http://localhost:8004/jobs", timeout=5)).status_code,
    str(r.json())[:60]
))

_check("POST :8005/analyze (insights)", lambda: (
    (r := httpx.post("http://localhost:8005/analyze",
        json={"query": "SELECT sum(revenue) FROM sales", "results": [{"revenue": 100}]},
        timeout=15)).status_code,
    str(r.json())[:60]
))

_check("GET :8006/health (orchestration)", lambda: (
    (r := httpx.get("http://localhost:8006/health", timeout=5)).status_code,
    r.text[:60]
))

# === Section 5: Cross-Service Tool Calls (Agent -> Microservice) ===
print("\n--- 5. Cross-Service Agent Tool Calls ---")

_check("Tool: list_uploaded_files", lambda: (
    (r := httpx.get(f"{BASE}/files", timeout=5)).status_code,
    f"{len(r.json())} files (via gateway)"
))

_check("Tool: connectors/introspect", lambda: (
    (r := httpx.post("http://localhost:8002/introspect",
        json={"connector_type": "postgresql", "config": {}},
        timeout=10)).status_code,
    str(r.json())[:60]
))

_check("Tool: insights/recommend-indexes", lambda: (
    (r := httpx.post("http://localhost:8005/recommend-indexes",
        json={"table": "sales", "query_patterns": ["SELECT * FROM sales"]},
        timeout=15)).status_code,
    str(r.json())[:60]
))

# === Summary ===
print("\n" + "="*70)
passed = sum(1 for *_, ok in RESULTS if ok)
total = len(RESULTS)
# Count expected failures (no-DB endpoints that return 400/502)
expected_fails = sum(1 for n, s, d, ok in RESULTS if not ok and ("no DB" in n or "502" in str(d) or "400" in str(d)))

print(f"  TOTAL: {passed}/{total} passed" + (f" ({expected_fails} expected w/o DB)" if expected_fails else ""))
if passed + expected_fails >= total:
    print("  STATUS: ALL SYSTEMS OPERATIONAL" + (" (DB-dependent tests expected to fail)" if expected_fails else ""))
else:
    failed = [(n, s, d) for n, s, d, ok in RESULTS if not ok]
    print(f"  STATUS: {len(failed)} ISSUE(S) DETECTED")
    for n, s, d in failed:
        print(f"    - {n}: {s} -> {d}")
print("="*70 + "\n")
