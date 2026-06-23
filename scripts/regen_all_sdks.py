"""
Sprint S21d — multi-service SDK regeneration orchestrator.

Walks every backend microservice, dumps its OpenAPI schema in a
subprocess-isolated Python process, then runs
``scripts/generate_sdk.py`` against each. The result is one
``sdk_clients/aura_<service>_client/`` per service plus a fresh
``aurabackend/<service>/openapi.json`` snapshot committed alongside.

Why subprocess isolation?
-------------------------
Importing all nine service ``main.py`` modules into the same Python
process is not safe: each ``create_service()`` call mutates shared
state (``logging`` config, the global Prometheus registry, env-var
caches), and lifespan side effects could leak across services.
Each service therefore gets its OWN fresh Python process via
``subprocess.run([sys.executable, "-c", probe])`` — no shared state,
no ordering bugs.

Why service-specific variable names?
------------------------------------
Each service main.py uses a unique FastAPI variable name
(``code_gen_app``, ``execution_app``, ``metadata_app``,
``scheduler_app``, …) so multiple services running in the same
process during dev wouldn't collide. The mapping below codifies it
so the orchestrator can import the right name per service.

Why commit the schemas?
-----------------------
With the schema committed at ``aurabackend/<service>/openapi.json``,
the ``sdk-codegen-sync`` CI lane can run this script and ``git diff
--exit-code`` to detect any drift between the live service surface
and the committed client. If a developer adds a new endpoint without
regenerating, CI fails with a clear "client out of date" signal.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "aurabackend"
SDK_DIR = REPO_ROOT / "sdk_clients"
GENERATE_SDK = REPO_ROOT / "scripts" / "generate_sdk.py"


# (service_dir, module_path, app_variable_name, service_tag, package_name)
#
# Excluded:
#   * api_gateway — has its own existing pipeline + committed
#     openapi.json at repo root; kept as the reference reference
#     client until S21e rolls it into the orchestrator.
#   * counterfactual_service — has the hand-written ``aura-counterfactual``
#     SDK with richer affordances (CLI, Jupyter repr, bulk replay,
#     crypto verification) that the codegen can't reproduce.
SERVICES: List[Tuple[str, str, str, str, str]] = [
    ("causal_service",          "causal_service.main",          "app",            "causal",          "aura_causal_client"),
    ("code_generation_service", "code_generation_service.main", "code_gen_app",   "code-generation", "aura_code_generation_client"),
    ("connectors",              "connectors.main",              "app",            "connectors",      "aura_connectors_client"),
    ("dar_service",             "dar_service.main",             "app",            "dar",             "aura_dar_client"),
    ("execution_sandbox_service", "execution_sandbox_service.main", "execution_app",  "execution-sandbox", "aura_execution_sandbox_client"),
    ("ingestion_service",       "ingestion_service.main",       "app",            "ingestion",       "aura_ingestion_client"),
    ("insights",                "insights.main",                "app",            "insights",        "aura_insights_client"),
    ("metadata_store",          "metadata_store.main",          "metadata_app",   "metadata-store",  "aura_metadata_store_client"),
    ("orchestration_service",   "orchestration_service.main",   "app",            "orchestration",   "aura_orchestration_client"),
    ("scheduler_service",       "scheduler_service.main",       "scheduler_app",  "scheduler",       "aura_scheduler_client"),
]


def dump_schema(module: str, app_var: str, output_path: Path) -> Tuple[bool, str]:
    """Dump one service's OpenAPI schema to ``output_path``.

    Runs in a fresh subprocess (cwd=aurabackend) so the service's
    import side effects don't leak. The probe writes the schema
    directly to ``output_path`` rather than stdout — services emit
    log lines on import (rate-limiter init, factory creation, etc.)
    and we don't want them polluting the JSON. Schema is
    canonical-JSON encoded (sort_keys=True, indent=2) for byte-
    stable diffs.

    Returns ``(success, message)``.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    probe = (
        "import json, os\n"
        f"from {module} import {app_var} as _app\n"
        "schema = _app.openapi()\n"
        "with open(os.environ['AURA_SCHEMA_OUT'], 'w', encoding='utf-8', newline='\\n') as f:\n"
        "    json.dump(schema, f, sort_keys=True, indent=2)\n"
        "    f.write('\\n')\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "AURA_SCHEMA_OUT": str(output_path),
    }
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        last = tail[-1] if tail else "(no stderr)"
        return (False, f"subprocess exit {result.returncode}: {last}")
    try:
        body = output_path.read_text(encoding="utf-8")
        schema = json.loads(body)
        paths = len(schema.get("paths", {}))
        schemas = len(schema.get("components", {}).get("schemas", {}))
        return (True, f"paths={paths} schemas={schemas}")
    except (json.JSONDecodeError, OSError) as exc:
        return (False, f"failed to read/parse {output_path}: {exc}")


def generate_client(schema_path: Path, output_dir: Path, package_name: str, service_tag: str) -> Tuple[bool, str]:
    """Run ``scripts/generate_sdk.py`` against one schema."""
    result = subprocess.run(
        [
            sys.executable, str(GENERATE_SDK),
            "--openapi", str(schema_path),
            "--output", str(output_dir),
            "--package-name", package_name,
            "--service-tag", service_tag,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        last = tail[-1] if tail else "(no stderr)"
        return (False, f"generate_sdk exit {result.returncode}: {last}")
    return (True, "ok")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate SDK clients for all AURA microservices.",
    )
    parser.add_argument(
        "--service", action="append", default=None,
        help="Limit to one or more service_dir names (repeatable). "
             "Default: all services in the SERVICES table.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero if ANY service fails. Default: log + continue "
             "so CI surfaces the first failure but still attempts the rest.",
    )
    args = parser.parse_args(argv)

    selected = SERVICES
    if args.service:
        wanted = set(args.service)
        selected = [s for s in SERVICES if s[0] in wanted]
        missing = wanted - {s[0] for s in SERVICES}
        if missing:
            print(f"warning: unknown services {sorted(missing)}", file=sys.stderr)

    failures: Dict[str, str] = {}
    for service_dir, module, app_var, tag, pkg in selected:
        print(f"=== {service_dir} ({tag}) ===", flush=True)
        schema_path = BACKEND / service_dir / "openapi.json"
        ok, msg = dump_schema(module, app_var, schema_path)
        if not ok:
            print(f"  schema dump FAILED: {msg}")
            failures[service_dir] = f"schema: {msg}"
            continue
        print(f"  schema dumped: {msg}")
        client_dir = SDK_DIR / pkg
        ok, msg = generate_client(schema_path, client_dir, pkg, tag)
        if not ok:
            print(f"  client gen FAILED: {msg}")
            failures[service_dir] = f"client: {msg}"
            continue
        print(f"  client generated: {client_dir}")

    print()
    print(f"summary: {len(selected) - len(failures)} ok, {len(failures)} failed")
    for name, why in failures.items():
        print(f"  {name}: {why}")

    if failures and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
