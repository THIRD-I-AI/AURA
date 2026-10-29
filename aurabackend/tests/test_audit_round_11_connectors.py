"""Audit round 11, connectors area: BUG-545, BUG-546, BUG-547, BUG-548."""
from __future__ import annotations

import socket

import duckdb
import pytest

from connectors.sql_limit import with_row_limit


def _table_of(n: int):
    con = duckdb.connect(":memory:")
    con.execute(f"CREATE TABLE t AS SELECT range AS v FROM range({n})")
    return con


# ── BUG-545: a caller-written LIMIT must not lift the connector's row cap ──

@pytest.mark.parametrize("query, expected", [
    ("SELECT * FROM t LIMIT ALL", "SELECT * FROM t LIMIT 10"),
    ("SELECT * FROM t LIMIT 999999999", "SELECT * FROM t LIMIT 10"),
    ("SELECT * FROM t LIMIT 999999999 OFFSET 5;", "SELECT * FROM t LIMIT 10 OFFSET 5"),
    ("SELECT * FROM t OFFSET 5 LIMIT ALL", "SELECT * FROM t OFFSET 5 LIMIT 10"),
    ("SELECT * FROM t LIMIT 5, 999999999", "SELECT * FROM t LIMIT 5, 10"),
    ("select * from t limit all -- everything", "select * from t limit 10"),
])
def test_a_larger_or_unbounded_limit_is_lowered_to_the_cap(query, expected):
    assert with_row_limit(query, 10) == expected


@pytest.mark.parametrize("query", ["SELECT v FROM t LIMIT ALL", "SELECT v FROM t LIMIT 999999999"])
def test_the_capped_query_returns_at_most_the_cap(query):
    assert len(_table_of(25).execute(with_row_limit(query, 10)).fetchall()) == 10


@pytest.mark.parametrize("query", [
    "SELECT * FROM t LIMIT 3", "SELECT * FROM t LIMIT 10", "SELECT * FROM t LIMIT 3 OFFSET 2",
    "SELECT * FROM t LIMIT 2, 3",
])
def test_a_smaller_limit_is_kept(query):
    assert with_row_limit(query, 10) == query


# ── BUG-548: the standard FETCH FIRST clause is a row limit too ──

@pytest.mark.parametrize("query, expected", [
    ("SELECT * FROM t ORDER BY v FETCH FIRST 3 ROWS ONLY", "SELECT * FROM t ORDER BY v FETCH FIRST 3 ROWS ONLY"),
    ("SELECT * FROM t FETCH NEXT 1 ROW ONLY;", "SELECT * FROM t FETCH NEXT 1 ROW ONLY"),
    ("SELECT * FROM t FETCH FIRST ROW ONLY", "SELECT * FROM t FETCH FIRST ROW ONLY"),
    ("SELECT * FROM t OFFSET 5 ROWS FETCH FIRST 3 ROWS ONLY", "SELECT * FROM t OFFSET 5 ROWS FETCH FIRST 3 ROWS ONLY"),
    ("SELECT * FROM t FETCH FIRST 999999 ROWS ONLY", "SELECT * FROM t FETCH FIRST 10 ROWS ONLY"),
])
def test_fetch_first_is_recognised_and_not_followed_by_a_second_limit(query, expected):
    assert with_row_limit(query, 10) == expected


@pytest.mark.parametrize("query, rows", [
    ("SELECT v FROM t ORDER BY v FETCH FIRST 3 ROWS ONLY", 3),
    ("SELECT v FROM t ORDER BY v FETCH FIRST 999999 ROWS ONLY", 10),
])
def test_a_fetch_first_query_still_runs(query, rows):
    assert len(_table_of(25).execute(with_row_limit(query, 10)).fetchall()) == rows


# ── BUG-546: MySQL identifiers must be backtick-quoted ──

def test_mysql_identifier_quoting_uses_backticks():
    from shared.sql_identifiers import quote_mysql_identifier

    assert quote_mysql_identifier("orders") == "`orders`"
    assert quote_mysql_identifier("a`b") == "`a``b`"
    with pytest.raises(ValueError):
        quote_mysql_identifier("a\x00b")


class _Cursor:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql):
        self.log.append(sql)

    async def fetchall(self):
        return []

    async def fetchone(self):
        return (0,)


class _Conn:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, *a):
        return _Cursor(self.log)


class _Pool:
    def __init__(self):
        self.log = []

    def acquire(self):
        return _Conn(self.log)


@pytest.mark.asyncio
async def test_mysql_schema_sample_and_profile_send_backtick_quoted_table_names():
    # No MySQL server in the test environment: record the SQL the connector sends
    # and check its quoting, which is the whole of the defect.
    from connectors import ConnectorConfig, SourceType
    from connectors.mysql_connector import MySQLConnector

    c = MySQLConnector(ConnectorConfig(source_type=SourceType.MYSQL, name="m", host="db.example.com"))
    c.pool = _Pool()
    c._is_connected = True
    await c.get_table_schema("orders")
    await c.sample_rows("orders", limit=5)
    await c.profile_table("orders")
    assert c.pool.log, "the connector sent no SQL"
    for sql in c.pool.log:
        assert '"orders"' not in sql, sql
        assert "`orders`" in sql, sql


# ── BUG-547: the address that passed the SSRF check is the one connected to ──

@pytest.mark.asyncio
async def test_the_connector_is_built_with_the_vetted_address_not_the_hostname(monkeypatch):
    from connectors import ConnectorConfig, SourceType
    from shared.connector_policy import open_connector

    monkeypatch.delenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", raising=False)
    answers = iter(["8.8.8.8", "127.0.0.1", "127.0.0.1"])
    lookups = []

    def rebinding_resolver(host, *a, **k):
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), 0))]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_resolver)
    built = []
    config = ConnectorConfig(source_type=SourceType.POSTGRESQL, name="p", host="rebind.attacker.example")
    await open_connector("postgresql", config, files_root=None, build=built.append)

    assert built[0].host == "8.8.8.8"
    assert lookups == ["rebind.attacker.example"]


@pytest.mark.asyncio
async def test_a_name_with_any_internal_address_is_still_refused(monkeypatch):
    from connectors import ConnectorConfig, SourceType
    from shared.connector_policy import ConnectorPolicyError, open_connector

    monkeypatch.delenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
    ])
    config = ConnectorConfig(source_type=SourceType.MYSQL, name="m", host="mixed.attacker.example")
    with pytest.raises(ConnectorPolicyError):
        await open_connector("mysql", config, files_root=None, build=lambda cfg: cfg)


@pytest.mark.asyncio
async def test_an_operator_allowed_private_host_is_passed_through_unchanged(monkeypatch):
    from connectors import ConnectorConfig, SourceType
    from shared.connector_policy import open_connector

    monkeypatch.setenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", "true")
    config = ConnectorConfig(source_type=SourceType.POSTGRESQL, name="p", host="db.internal")
    built = await open_connector("postgresql", config, files_root=None, build=lambda cfg: cfg)
    assert built.host == "db.internal"
