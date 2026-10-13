"""Connectors are built in one place: shared.connector_policy.open_connector.

Closes the class behind BUG-232, 357, 367, 377, 404, 441 and 442 -- a route that
builds a connector from a caller's config without the host rule or the file
confinement. This test reads the source of the whole backend and fails if anything
outside the allowed places constructs a connector, so a new route cannot skip the
door by accident.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from connectors import ConnectorConfig, SourceType
from shared.connector_policy import ConnectorPolicyError, open_connector

_BACKEND = Path(__file__).resolve().parents[1]

# relative path -> why constructing a connector there is acceptable
_ALLOWED = {
    "shared/connector_policy.py": "the door itself",
    "connectors/registry.py": "the factories the door calls",
    "connectors/main.py": "_create_connector: the connectors service's builder, called by the door "
                          "for request configs and directly only for the operator's own DB_* environment",
}
_SKIP_DIRS = {"tests", "__pycache__", ".venv", "node_modules", "alembic", "scripts"}


def _constructs_a_connector(call: ast.Call) -> bool:
    func = call.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if name in ("build_connector", "_create_connector"):
        return True
    return name.endswith("Connector") and name[:1].isupper()


def _offenders():
    found = []
    for path in sorted(_BACKEND.rglob("*.py")):
        rel = path.relative_to(_BACKEND).as_posix()
        if rel in _ALLOWED or _SKIP_DIRS & set(path.relative_to(_BACKEND).parts):
            continue
        # A connector module may construct its own helpers; it is not a call site.
        if rel.startswith("connectors/") and rel.endswith("_connector.py"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _constructs_a_connector(node):
                found.append(f"{rel}:{node.lineno}")
    return found


def test_nothing_builds_a_connector_except_through_the_door():
    assert _offenders() == [], (
        "these call sites construct a connector directly; open it with "
        "shared.connector_policy.open_connector instead"
    )


# Raw driver connections bypass the connector classes altogether, so they are pinned
# too: relative path -> where the target comes from and what checks it.
_RAW_DRIVER_SITES = {
    "connectors/postgresql_connector.py": "the connector class itself, built by the door",
    "connectors/mysql_connector.py": "the connector class itself, built by the door",
    "api_gateway/routers/queries.py": "pool for a config that open_connector has just checked",
    "pipeline/streaming/sinks/database_sink.py": "calls shared.ssrf.is_allowed_db_host on its own target first",
    "shared/database_adapter.py": "vault adapter; target comes from the operator's AURA_VAULT_* environment",
}
_DRIVERS = {"asyncpg", "aiomysql", "psycopg", "psycopg2", "pymysql"}


def test_raw_database_driver_connections_are_only_where_reviewed():
    found = set()
    for path in sorted(_BACKEND.rglob("*.py")):
        if _SKIP_DIRS & set(path.relative_to(_BACKEND).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            if (
                isinstance(node, ast.Call) and isinstance(func, ast.Attribute)
                and func.attr in ("connect", "create_pool")
                and isinstance(func.value, ast.Name) and func.value.id in _DRIVERS
            ):
                found.add(path.relative_to(_BACKEND).as_posix())
    assert found - set(_RAW_DRIVER_SITES) == set(), "a new raw database connection needs a host check and a review"
    assert set(_RAW_DRIVER_SITES) - found == set(), "stale entry: remove it from _RAW_DRIVER_SITES"


def _never(cfg):
    raise AssertionError("a connector was built for a refused config")


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "localhost", "", None])
async def test_the_door_refuses_internal_and_missing_hosts(monkeypatch, host):
    monkeypatch.delenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", raising=False)
    config = ConnectorConfig(source_type=SourceType.POSTGRESQL, name="t", host=host)
    with pytest.raises(ConnectorPolicyError, match="public address"):
        await open_connector("postgresql", config, files_root=None, build=_never)


@pytest.mark.asyncio
async def test_the_door_confines_server_file_paths(tmp_path):
    own = tmp_path / "uploads" / "tenant-a"
    own.mkdir(parents=True)

    def cfg(**fields):
        return ConnectorConfig(source_type=SourceType("duckdb"), name="t", **fields)

    inside = await open_connector("duckdb", cfg(database="mine.duckdb"), files_root=str(own), build=lambda c: c)
    assert inside.database == str((own / "mine.duckdb").resolve())
    assert (await open_connector("duckdb", cfg(database=":memory:"), files_root=None, build=lambda c: c)).database == ":memory:"

    for outside in (str(tmp_path / "uploads" / "tenant-b" / "theirs.duckdb"), "../tenant-b/theirs.duckdb"):
        with pytest.raises(ConnectorPolicyError, match="uploads"):
            await open_connector("duckdb", cfg(database=outside), files_root=str(own), build=_never)
    with pytest.raises(ConnectorPolicyError):
        await open_connector("duckdb", cfg(extra_params={"db_path": str(tmp_path / "x.duckdb")}),
                             files_root=str(own), build=_never)
    # No local directory at all (remote storage): only ':memory:' passes.
    with pytest.raises(ConnectorPolicyError):
        await open_connector("duckdb", cfg(database="mine.duckdb"), files_root=None, build=_never)
