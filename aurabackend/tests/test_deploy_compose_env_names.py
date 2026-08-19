"""The deploy compose file may only set env vars the backend actually reads.

WHY THIS EXISTS: an env var the code does not read is not an error. Docker
accepts it, the process starts, the *default* silently wins — and for anything
pointing at state, the default puts that state inside the container, where the
next ``compose pull`` deletes it. There is no log line and no failing request.

That happened twice on the first production deploy. Both names were plausible
inventions that matched nothing:

    AURA_DATABASE_URL   (code reads GATEWAY_DATABASE_URL)  -> gateway.db lived
                        in the container; query history and file metadata were
                        destroyed on every image update.
    AURA_UPLOAD_DIR     (code reads AURA_UPLOADS_ROOT)     -> every dataset a
                        user uploaded lived in the container and died with it.

Both were found by listing the volume and noticing it was EMPTY while the app
reported everything working. A test asserting "the endpoint returns 200" can
never catch this; the deployment is healthy right up until the redeploy.

So this test reads the compose file and checks each env var name against the
backend source. It is a spelling check, deliberately not a behavioural one —
the failure mode here is a name that matches nothing, and matching is exactly
what a grep can prove.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

yaml = pytest.importorskip("yaml", reason="pyyaml needed to parse the compose file")

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
_COMPOSE = _REPO / "deploy" / "aws-free-tier" / "docker-compose.yml"

# Names consumed by the runtime/interpreter rather than by AURA code. A var
# here is exempt from needing a reader in our source tree.
_NOT_OURS = {
    "PATH", "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "TZ", "LANG", "LC_ALL", "HOME", "PORT",
}


def _compose() -> dict:
    if not _COMPOSE.exists():
        pytest.skip(f"deploy compose not present at {_COMPOSE}")
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _env_names(service: dict) -> list:
    """Env var names from a service, accepting both compose spellings
    (``KEY: value`` mapping and ``- KEY=value`` list)."""
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return list(env.keys())
    return [str(item).split("=", 1)[0] for item in env]


def _python_sources() -> list:
    """Backend source text, skipping tests — a name referenced only by a test
    is not proof that production code reads it."""
    out = []
    for path in _BACKEND.rglob("*.py"):
        parts = set(path.parts)
        if "tests" in parts or "__pycache__" in parts or ".venv" in parts:
            continue
        try:
            out.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return out


@pytest.fixture(scope="module")
def sources() -> list:
    return _python_sources()


# Services whose environment is consumed by OUR Python code. caddy is excluded
# deliberately: its AURA_DOMAIN is read by the Caddyfile ({$AURA_DOMAIN}), not
# by the backend, so demanding a Python reader for it would be wrong.
_PYTHON_SERVICES = ("api_gateway", "uasr_service")


def _gateway_env_names() -> list:
    services = _compose().get("services", {})
    names = []
    for service_name in _PYTHON_SERVICES:
        svc = services.get(service_name)
        if not svc:
            continue
        names.extend(f"{service_name}:{n}" for n in _env_names(svc) if n not in _NOT_OURS)
    return names


def test_compose_defines_gateway_env_vars():
    """Guards the test itself: if the service or its env block is renamed,
    the parametrised check below would silently pass on an empty list."""
    assert _gateway_env_names(), "no api_gateway env vars found — did the compose shape change?"


@pytest.mark.parametrize("qualified", _gateway_env_names())
def test_every_compose_env_var_is_read_by_the_backend(qualified, sources):
    """Each name must appear literally in non-test backend source — as
    ``os.getenv("NAME")``, a pydantic ``alias="NAME"``, or equivalent."""
    service, name = qualified.split(":", 1)
    pattern = re.compile(rf"['\"]{re.escape(name)}['\"]")
    if any(pattern.search(text) for text in sources):
        return
    pytest.fail(
        f"{name} is set on the {service} service in the deploy compose file "
        f"but no non-test backend "
        f"source references it. Docker will accept it and the code will "
        f"silently use its default instead — which for a state path means "
        f"the data lands inside the container and is destroyed on the next "
        f"`compose pull`. Grep the backend for the name the code actually "
        f"reads and use that spelling."
    )


@pytest.mark.parametrize("service_name", _PYTHON_SERVICES)
def test_state_paths_point_at_the_mounted_volume(service_name):
    """A correctly-spelled var pointing at a container-local path loses the
    data just as thoroughly as a misspelled one."""
    svc = _compose().get("services", {}).get(service_name)
    if not svc:
        pytest.skip(f"{service_name} is not defined in this compose file")
    env = svc.get("environment") or {}
    if not isinstance(env, dict):
        pytest.skip("list-form environment carries no values to check")

    mounts = [str(v) for v in (svc.get("volumes") or [])]
    assert any(m.endswith(":/data") for m in mounts), "expected the aura-data volume mounted at /data"

    for name, value in env.items():
        text = str(value)
        # _URL alone is too broad — AURA_UASR_URL is a service address, not a
        # path. Only DB URLs and explicit directory/root vars name state.
        if not ("DATABASE_URL" in name or name.endswith(("_DIR", "_ROOT", "_PATH"))):
            continue
        # sqlite+aiosqlite:////data/x.db -> the path part starts after the scheme
        path = text.split("://", 1)[-1] if "://" in text else text
        assert path.startswith("/data") or path.startswith("//data"), (
            f"{service_name}: {name}={text} does not live under the mounted "
            f"/data volume, so whatever it points at is destroyed when the "
            f"container is replaced."
        )
