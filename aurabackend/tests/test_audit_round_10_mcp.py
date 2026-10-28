"""Audit round 10, MCP server: BUG-520, BUG-521, BUG-522, BUG-542."""
from __future__ import annotations

import sys

import duckdb
import pytest

pytest.importorskip("mcp")

import uvicorn  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from mcp_servers import aura_mcp_server as srv  # noqa: E402


@pytest.fixture
def captured_uvicorn(monkeypatch):
    """Stop the SSE transport at the point it would start listening and record its config."""
    captured = {}

    async def _fake_serve(self, sockets=None):
        captured["config"] = self.config
        self.started = True

    monkeypatch.setattr(uvicorn.Server, "serve", _fake_serve)
    return captured


def _run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["aura-mcp-server", *argv])
    srv.main()


# ── BUG-520 ──────────────────────────────────────────────────────────

def test_bug520_http_port_and_host_reach_the_listener(monkeypatch, captured_uvicorn):
    monkeypatch.setenv("AURA_MCP_API_KEY", "k" * 32)
    monkeypatch.delenv("FASTMCP_HOST", raising=False)
    monkeypatch.delenv("FASTMCP_PORT", raising=False)

    _run_main(monkeypatch, "--http", "9123")

    cfg = captured_uvicorn["config"]
    assert cfg.port == 9123
    assert cfg.host == "0.0.0.0"


def test_bug520_in_cluster_host_header_is_not_rejected_by_localhost_rebinding_guard():
    # Binding 0.0.0.0 with the localhost-only DNS-rebinding allow-list would 421 every
    # in-cluster request whose Host is the service name.
    server = srv.build_server(host="0.0.0.0", port=9123)
    ts = server.settings.transport_security
    assert ts is None or not ts.enable_dns_rebinding_protection


# ── BUG-521 ──────────────────────────────────────────────────────────

@pytest.fixture
def duck_tool(tmp_path, monkeypatch):
    db = tmp_path / "lake.duckdb"
    seed = duckdb.connect(str(db))
    seed.execute("CREATE TABLE a AS SELECT * FROM (VALUES ('east'), ('west')) t(region)")
    seed.execute("CREATE TABLE b AS SELECT * FROM (VALUES ('west'), ('north')) t(region)")
    seed.close()
    monkeypatch.setattr(srv, "DUCKDB_PATH", str(db))
    monkeypatch.setattr(srv, "_duck_con", None)
    yield srv.build_server()._tool_manager.get_tool("duckdb_query").fn
    if srv._duck_con is not None:
        srv._duck_con.close()
        srv._duck_con = None


@pytest.mark.parametrize("sql, expected", [
    ("SELECT region FROM a UNION ALL SELECT region FROM b", ["east", "north", "west", "west"]),
    ("SELECT region FROM a EXCEPT SELECT region FROM b", ["east"]),
    ("SELECT region FROM a INTERSECT SELECT region FROM b", ["west"]),
    ("WITH u AS (SELECT region FROM a) SELECT region FROM u UNION SELECT region FROM b",
     ["east", "north", "west"]),
])
def test_bug521_set_operations_are_accepted(duck_tool, sql, expected):
    out = duck_tool(sql=sql)
    assert sorted(r[0] for r in out["rows"]) == expected


@pytest.mark.parametrize("sql", ["SELECT 1 AS x;", "SELECT 1 AS x -- trailing comment"])
def test_bug521_trailing_semicolon_or_comment_does_not_break_the_row_cap_wrapper(duck_tool, sql):
    out = duck_tool(sql=sql)
    assert out["rows"] == [[1]]


def test_bug521_set_operation_with_dml_still_rejected():
    with pytest.raises(ValueError):
        srv._assert_select_only("SELECT 1 UNION SELECT 2; DROP TABLE a")


# ── BUG-522 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["password", "sslpassword", "secret", "token", "api_key", "apikey", "auth", "credential"])
def test_bug522_query_string_credentials_are_redacted(key):
    dsn = f"postgresql+asyncpg://aura@db:5432/aura?{key}=s3cret&sslmode=require"
    out = srv._redact_dsn(dsn)
    assert "s3cret" not in out
    assert "sslmode=require" in out
    assert "db:5432/aura" in out


# ── BUG-542 ──────────────────────────────────────────────────────────

def test_bug542_http_transport_refuses_to_start_without_api_key(monkeypatch, captured_uvicorn):
    monkeypatch.delenv("AURA_MCP_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, "--http", "9124")
    assert "config" not in captured_uvicorn


def test_bug542_http_requests_without_the_key_are_rejected(monkeypatch, captured_uvicorn):
    key = "test-key-" + "x" * 24
    monkeypatch.setenv("AURA_MCP_API_KEY", key)
    _run_main(monkeypatch, "--http", "9125")
    app = captured_uvicorn["config"].app

    client = TestClient(app, base_url="http://aura-mcp:9125")
    assert client.get("/sse").status_code == 401
    assert client.post("/messages/", json={}).status_code == 401
    assert client.post("/messages/", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    # With the key the request reaches the MCP transport, which rejects the missing session_id.
    authed = client.post("/messages/", json={}, headers={"Authorization": f"Bearer {key}"})
    assert authed.status_code == 400
