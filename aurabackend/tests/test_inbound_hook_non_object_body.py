"""BUG-243: firing a hook with a JSON body that is not an object (webhook senders
commonly batch events as an array) raised AttributeError -> 500, after the fire had
already been counted and announced."""
import pytest

from tests.test_inbound_hook_target_ownership import env  # noqa: F401  (fixture)

V1 = "/api/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[{"event": "a"}, {"event": "b"}], "text", 7])
async def test_non_object_json_reaches_the_trigger_as_an_object(env, monkeypatch, body):  # noqa: F811
    client, _, router_mod = env
    client.post(f"{V1}/hooks", json={"slug": "batch", "kind": "pipeline", "target": "pipe_own"})
    seen = []

    async def _capture(pipeline_id, payload, owner_workspace_id):
        seen.append(payload)
        return {"status": "ok", "preview_only": payload.get("preview_only", False)}

    monkeypatch.setattr(router_mod, "_fire_pipeline", _capture)

    r = client.post(f"{V1}/hooks/fire/batch", json=body)

    assert r.status_code == 200, r.text
    assert seen == [{"_body": body}]


@pytest.mark.asyncio
async def test_an_object_body_is_passed_through_unchanged(env, monkeypatch):  # noqa: F811
    client, _, router_mod = env
    client.post(f"{V1}/hooks", json={"slug": "obj", "kind": "pipeline", "target": "pipe_own"})
    seen = []

    async def _capture(pipeline_id, payload, owner_workspace_id):
        seen.append(payload)
        return {"status": "ok"}

    monkeypatch.setattr(router_mod, "_fire_pipeline", _capture)
    assert client.post(f"{V1}/hooks/fire/obj", json={"preview_only": True}).status_code == 200
    assert seen == [{"preview_only": True}]


@pytest.mark.asyncio
async def test_an_oversized_fire_body_is_refused_before_it_is_read(env, monkeypatch):  # noqa: F811
    # BUG-379: this public route buffered the whole body before checking the signature,
    # so one large unauthenticated POST could OOM the single gateway worker.
    client, _, router_mod = env
    client.post(f"{V1}/hooks", json={"slug": "big", "kind": "pipeline", "target": "pipe_own", "secret": "s"})
    fired = []

    async def _capture(pipeline_id, payload, owner_workspace_id):
        fired.append(payload)
        return {"status": "ok"}

    monkeypatch.setattr(router_mod, "_fire_pipeline", _capture)
    monkeypatch.setenv("AURA_HOOK_MAX_BODY_BYTES", "2048")

    r = client.post(f"{V1}/hooks/fire/big", content=b"x" * 10_000, headers={"content-type": "application/json"})

    assert r.status_code == 413, r.text
    assert fired == []
