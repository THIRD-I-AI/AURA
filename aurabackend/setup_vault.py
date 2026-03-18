#!/usr/bin/env python3
"""
AURA Vault — Automated Schema Deployment
=========================================
Connects to the target PostgreSQL instance and deploys the full hybrid
multimodal schema (regular data + pgvector + PostGIS).

Usage
-----
  # Deploy using .env defaults
  python setup_vault.py

  # Deploy to a specific host
  python setup_vault.py --host 192.168.1.50 --port 5432 --user postgres --password mypass

  # Verify only (no schema changes)
  python setup_vault.py --verify-only
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Allow imports from aurabackend root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Try to load .env if python-dotenv is available
try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────
#  Defaults from AURA_VAULT_* env vars (or fallback)
# ─────────────────────────────────────────────────────────────
DEFAULT_HOST = os.getenv("AURA_VAULT_HOST", "localhost")
DEFAULT_PORT = int(os.getenv("AURA_VAULT_PORT", "5432"))
DEFAULT_USER = os.getenv("AURA_VAULT_USER", "postgres")
DEFAULT_PASS = os.getenv("AURA_VAULT_PASSWORD", "")
DEFAULT_DB   = os.getenv("AURA_VAULT_DATABASE", "aura_vault")

SCHEMA_FILE = Path(__file__).resolve().parent / "database" / "aura_vault_schema.sql"


# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────

def _banner(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}\n")


def _ok(msg: str) -> None:
    print(f"  ✅  {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠️  {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌  {msg}")


# ─────────────────────────────────────────────────────────────
#  Core
# ─────────────────────────────────────────────────────────────

async def ensure_database(host: str, port: int, user: str, password: str, db: str) -> bool:
    """Create the vault database if it doesn't exist."""
    import asyncpg  # type: ignore[import-untyped]

    try:
        # Connect to default 'postgres' database to check / create
        conn = await asyncpg.connect(
            host=host, port=port, user=user, password=password, database="postgres",
        )
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db,
        )
        if not exists:
            # CREATE DATABASE can't run inside a transaction
            await conn.execute(f'CREATE DATABASE "{db}"')
            _ok(f"Database '{db}' created")
        else:
            _ok(f"Database '{db}' already exists")
        await conn.close()
        return True
    except Exception as exc:
        _fail(f"Could not ensure database: {exc}")
        return False


async def deploy_schema(host: str, port: int, user: str, password: str, db: str) -> bool:
    """Read aura_vault_schema.sql and execute it against the vault database."""
    import asyncpg  # type: ignore[import-untyped]

    if not SCHEMA_FILE.exists():
        _fail(f"Schema file not found: {SCHEMA_FILE}")
        return False

    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    print(f"  📄  Schema file: {SCHEMA_FILE}  ({len(sql):,} bytes)")

    try:
        conn = await asyncpg.connect(
            host=host, port=port, user=user, password=password, database=db,
        )
        await conn.execute(sql)
        _ok("Schema deployed successfully")
        await conn.close()
        return True
    except Exception as exc:
        _fail(f"Schema deployment failed: {exc}")
        return False


async def verify_schema(host: str, port: int, user: str, password: str, db: str) -> dict:
    """Verify that all expected tables, extensions, and functions exist."""
    import asyncpg  # type: ignore[import-untyped]

    report: dict = {"extensions": {}, "tables": {}, "functions": {}, "views": {}}

    # Expected objects
    expected_extensions = ["uuid-ossp", "postgis", "vector"]
    expected_tables = [
        "users", "data_sources", "transactions", "audit_log",
        "agent_memory", "saved_queries", "pipelines",
        "image_assets", "vr_telemetry", "vr_environments",
        "vr_objects", "vector_store",
    ]
    expected_views = ["vr_purchase_activity"]
    expected_functions = ["find_similar_images", "update_timestamp"]

    try:
        conn = await asyncpg.connect(
            host=host, port=port, user=user, password=password, database=db,
        )

        # Extensions
        rows = await conn.fetch("SELECT extname FROM pg_extension")
        installed = {r["extname"] for r in rows}
        for ext in expected_extensions:
            present = ext in installed
            report["extensions"][ext] = present
            (_ok if present else _warn)(f"Extension '{ext}': {'installed' if present else 'MISSING'}")

        # Tables
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        existing_tables = {r["table_name"] for r in rows}
        for tbl in expected_tables:
            present = tbl in existing_tables
            report["tables"][tbl] = present
            (_ok if present else _warn)(f"Table '{tbl}': {'exists' if present else 'MISSING'}")

        # Views
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_schema = 'public'"
        )
        existing_views = {r["table_name"] for r in rows}
        for view in expected_views:
            present = view in existing_views
            report["views"][view] = present
            (_ok if present else _warn)(f"View '{view}': {'exists' if present else 'MISSING'}")

        # Functions
        rows = await conn.fetch(
            "SELECT routine_name FROM information_schema.routines "
            "WHERE routine_schema = 'public'"
        )
        existing_fns = {r["routine_name"] for r in rows}
        for fn in expected_functions:
            present = fn in existing_fns
            report["functions"][fn] = present
            (_ok if present else _warn)(f"Function '{fn}': {'exists' if present else 'MISSING'}")

        # Row counts for each table
        print("\n  📊  Table row counts:")
        for tbl in sorted(existing_tables & set(expected_tables)):
            try:
                count = await conn.fetchval(f'SELECT COUNT(*) FROM "{tbl}"')
                print(f"      {tbl}: {count:,} rows")
            except Exception:
                print(f"      {tbl}: (could not count)")

        await conn.close()

        # Summary
        all_ok = (
            all(report["extensions"].values())
            and all(report["tables"].values())
            and all(report["views"].values())
            and all(report["functions"].values())
        )
        report["all_ok"] = all_ok
        return report

    except Exception as exc:
        _fail(f"Verification failed: {exc}")
        report["error"] = str(exc)
        report["all_ok"] = False
        return report


async def test_vector_search(host: str, port: int, user: str, password: str, db: str) -> bool:
    """Quick smoke test: insert + search a dummy vector."""
    import asyncpg  # type: ignore[import-untyped]

    try:
        conn = await asyncpg.connect(
            host=host, port=port, user=user, password=password, database=db,
        )
        # Check if pgvector is available
        has_vector = await conn.fetchval(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        )
        if not has_vector:
            _warn("pgvector not installed — skipping vector smoke test")
            await conn.close()
            return True

        # Insert a dummy embedding into vector_store
        dummy_vec = "[" + ",".join(["0.1"] * 1536) + "]"
        await conn.execute(
            "INSERT INTO vector_store (collection, content, embedding) "
            "VALUES ($1, $2, $3::vector) "
            "ON CONFLICT DO NOTHING",
            "smoke_test", "setup_vault test row", dummy_vec,
        )

        # Search for it
        rows = await conn.fetch(
            "SELECT vector_id, 1 - (embedding <=> $1::vector) AS similarity "
            "FROM vector_store WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> $1::vector LIMIT 1",
            dummy_vec,
        )
        if rows:
            _ok(f"Vector search works — similarity: {rows[0]['similarity']:.4f}")
        else:
            _warn("Vector search returned no results")

        # Clean up
        await conn.execute(
            "DELETE FROM vector_store WHERE collection = 'smoke_test'"
        )

        await conn.close()
        return True
    except Exception as exc:
        _warn(f"Vector smoke test failed (non-fatal): {exc}")
        return False


async def test_spatial(host: str, port: int, user: str, password: str, db: str) -> bool:
    """Quick smoke test: insert + query a spatial point."""
    import asyncpg  # type: ignore[import-untyped]

    try:
        conn = await asyncpg.connect(
            host=host, port=port, user=user, password=password, database=db,
        )
        has_postgis = await conn.fetchval(
            "SELECT 1 FROM pg_extension WHERE extname = 'postgis'"
        )
        if not has_postgis:
            _warn("PostGIS not installed — skipping spatial smoke test")
            await conn.close()
            return True

        # Just test that ST_MakePoint works
        row = await conn.fetchrow(
            "SELECT ST_AsText(ST_SetSRID(ST_MakePoint(1.0, 2.0, 3.0), 4326)) AS pt"
        )
        if row:
            _ok(f"PostGIS works — {row['pt']}")
        else:
            _warn("PostGIS query returned no result")

        await conn.close()
        return True
    except Exception as exc:
        _warn(f"Spatial smoke test failed (non-fatal): {exc}")
        return False


# ─────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> int:
    _banner("AURA Vault — Schema Deployment")
    print(f"  Target: {args.user}@{args.host}:{args.port}/{args.database}")
    print(f"  Mode:   {'verify-only' if args.verify_only else 'full deploy'}")

    # Ensure asyncpg is importable
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        _fail("asyncpg is not installed.  Run: pip install asyncpg")
        return 1

    if args.verify_only:
        _banner("Verification")
        report = await verify_schema(
            args.host, args.port, args.user, args.password, args.database,
        )
        return 0 if report.get("all_ok") else 1

    # Step 1: Ensure database exists
    _banner("Step 1 — Ensure Database")
    if not await ensure_database(args.host, args.port, args.user, args.password, args.database):
        return 1

    # Step 2: Deploy schema
    _banner("Step 2 — Deploy Schema")
    if not await deploy_schema(args.host, args.port, args.user, args.password, args.database):
        return 1

    # Step 3: Verify
    _banner("Step 3 — Verify Schema")
    report = await verify_schema(args.host, args.port, args.user, args.password, args.database)

    # Step 4: Smoke tests
    _banner("Step 4 — Smoke Tests")
    await test_vector_search(args.host, args.port, args.user, args.password, args.database)
    await test_spatial(args.host, args.port, args.user, args.password, args.database)

    # Summary
    _banner("Deployment Complete")
    if report.get("all_ok"):
        _ok("All tables, extensions, views, and functions verified ✨")
        print("\n  Next steps:")
        print("    1. Update .env with AURA_VAULT_HOST=<your-laptop-ip>")
        print("    2. Start the connector service: python -m connectors.main")
        print("    3. Test: curl http://localhost:8002/vault/health")
        return 0
    else:
        _warn("Some objects are missing — check the output above")
        return 1


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy AURA Vault schema to PostgreSQL",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"DB host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"DB port (default: {DEFAULT_PORT})")
    parser.add_argument("--user", default=DEFAULT_USER, help=f"DB user (default: {DEFAULT_USER})")
    parser.add_argument("--password", default=DEFAULT_PASS, help="DB password")
    parser.add_argument("--database", default=DEFAULT_DB, help=f"DB name (default: {DEFAULT_DB})")
    parser.add_argument("--verify-only", action="store_true", help="Only verify, don't deploy")
    args = parser.parse_args()
    code = asyncio.run(main(args))
    sys.exit(code)


if __name__ == "__main__":
    cli()
