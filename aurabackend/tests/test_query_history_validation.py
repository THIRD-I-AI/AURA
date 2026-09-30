"""BUG-233: POST /query-history validates its body and never lets the client choose the
row's primary key."""
from fastapi.testclient import TestClient

URL = "/api/v1/query-history"


def _client():
    from api_gateway.main import app

    return TestClient(app)


def test_client_supplied_id_is_ignored_and_an_overlong_one_cannot_break_the_insert():
    r = _client().post(URL, json={"id": "x" * 500, "prompt": "p", "sql": "SELECT 1",
                                  "status": "success", "rows": 1, "executionTime": 1.0})
    assert r.status_code == 200, r.text
    assert r.json()["id"] != "x" * 500


def test_reusing_an_id_is_not_a_500():
    c = _client()
    body = {"id": "dup", "prompt": "p", "sql": "SELECT 1", "status": "success", "rows": 1, "executionTime": 1.0}
    assert c.post(URL, json=body).status_code == 200
    assert c.post(URL, json=body).status_code == 200


def test_out_of_bounds_fields_are_rejected():
    c = _client()
    assert c.post(URL, json={"prompt": "p" * 10_001}).status_code == 422
    assert c.post(URL, json={"rows": -1}).status_code == 422
    assert c.post(URL, json={"status": "s" * 33}).status_code == 422


def test_the_frontends_shape_still_works():
    r = _client().post(URL, json={"prompt": "p", "sql": "SELECT 1", "status": "success",
                                  "rows": 3, "executionTime": 12.5})
    assert r.status_code == 200 and r.json()["success"] is True
