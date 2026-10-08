"""BUG-232: DuckDB/FAISS connector configs name a path on the server's disk; it must be
':memory:' or inside the caller's own uploads -- never another tenant's database file."""
import duckdb
import pytest

from tests.test_connections_row_ceiling import _isolated_uploads, client  # noqa: F401  (fixtures)

V1 = "/api/v1"


def _make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE secrets AS SELECT 42 AS v")
    con.close()
    return str(path)


def test_absolute_path_outside_own_uploads_is_refused(client, tmp_path):  # noqa: F811
    other = _make_db(tmp_path / "uploads" / "other-tenant" / "warehouse.duckdb")
    r = client.post(f"{V1}/connectors/duckdb/tables", json={"database": other})
    assert r.status_code == 400, r.text


def test_traversal_out_of_own_uploads_is_refused(client, tmp_path):  # noqa: F811
    _make_db(tmp_path / "uploads" / "other-tenant" / "warehouse.duckdb")
    r = client.post(f"{V1}/connectors/duckdb/tables",
                    json={"database": "../other-tenant/warehouse.duckdb"})
    assert r.status_code == 400, r.text


def test_saving_a_connection_to_an_outside_path_is_refused(client, tmp_path):  # noqa: F811
    other = _make_db(tmp_path / "uploads" / "other-tenant" / "warehouse.duckdb")
    r = client.post(f"{V1}/connections", json={"name": "x", "type": "duckdb", "database": other})
    assert r.status_code == 400, r.text


def test_own_uploaded_database_still_works(client, tmp_path):  # noqa: F811
    _make_db(tmp_path / "uploads" / "default" / "mine.duckdb")
    r = client.post(f"{V1}/connectors/duckdb/tables", json={"database": "mine.duckdb"})
    assert r.status_code == 200, r.text
    assert "secrets" in [t if isinstance(t, str) else t.get("name") for t in r.json()["tables"]]


def test_in_memory_is_allowed(client):  # noqa: F811
    r = client.post(f"{V1}/connectors/duckdb/test", json={"database": ":memory:"})
    assert r.status_code == 200 and r.json()["success"] is True, r.text


def test_nonexistent_outside_path_is_not_created(client, tmp_path):  # noqa: F811
    target = tmp_path / "elsewhere" / "new.duckdb"
    r = client.post(f"{V1}/connectors/duckdb/test", json={"database": str(target)})
    assert r.status_code == 400
    assert not target.exists()


@pytest.mark.parametrize("route", ["tables", "test", "profile"])
def test_extra_params_db_path_is_confined_too(client, tmp_path, route):  # noqa: F811
    # BUG-357: DuckDBConnector opens extra_params["db_path"] ahead of `database`, and only
    # connection_string/database were confined, so this opened another tenant's file.
    other = _make_db(tmp_path / "uploads" / "other-tenant" / "warehouse.duckdb")
    body = {"extra_params": {"db_path": other}}
    if route == "profile":
        body = {"connector_type": "duckdb", "connector_config": body, "table_name": "secrets"}
    r = client.post(f"{V1}/connectors/duckdb/{route}", json=body)
    assert r.status_code == 400, r.text
    assert "secrets" not in r.text


def test_extra_params_db_path_outside_uploads_is_not_created(client, tmp_path):  # noqa: F811
    target = tmp_path / "elsewhere" / "new.duckdb"
    r = client.post(f"{V1}/connectors/duckdb/test", json={"extra_params": {"db_path": str(target)}})
    assert r.status_code == 400
    assert not target.exists()


def test_own_database_via_extra_params_still_works(client, tmp_path):  # noqa: F811
    _make_db(tmp_path / "uploads" / "default" / "mine.duckdb")
    r = client.post(f"{V1}/connectors/duckdb/tables", json={"extra_params": {"db_path": "mine.duckdb"}})
    assert r.status_code == 200, r.text
