"""BUG-367: Postgres/MySQL connector hosts were not SSRF-filtered, and an empty host became
the gateway's own localhost, so the connector routes reached the gateway's internal network
(and worked as a port scanner)."""
import pytest

from tests.test_connections_row_ceiling import _isolated_uploads, client  # noqa: F401  (fixtures)

V1 = "/api/v1"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.12", "169.254.169.254", "100.64.0.1", "::1", ""])
@pytest.mark.parametrize("db", ["postgresql", "mysql"])
def test_ad_hoc_connector_routes_refuse_internal_hosts(client, host, db):  # noqa: F811
    r = client.post(f"{V1}/connectors/{db}/test", json={"host": host, "port": 5432, "database": "x"})
    assert r.status_code == 400, r.text
    assert "public address" in r.text


def test_saving_a_connection_to_an_internal_host_is_refused(client):  # noqa: F811
    r = client.post(f"{V1}/connections", json={"name": "x", "type": "postgresql", "host": "10.0.0.12", "port": 5432})
    assert r.status_code == 400, r.text


def test_private_hosts_can_be_allowed_by_the_operator(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", "true")
    r = client.post(f"{V1}/connectors/postgresql/test", json={"host": "127.0.0.1", "port": 1, "database": "x"})
    assert r.status_code != 400 or "public address" not in r.text
