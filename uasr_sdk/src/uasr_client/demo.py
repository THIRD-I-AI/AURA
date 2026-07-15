"""
End-to-end UASR demo — the one-command "watch it heal" scenario.

    uasr demo                     # boots the real service in-process, no server
    uasr demo --live http://host  # runs against an already-running service

The scenario:
  1. register a baseline for a source from healthy data
  2. push two healthy batches      -> accepted clean
  3. push a x100 unit-bug batch     -> detected as critical drift
  4. the semantic gate + healer act -> shim deployed / recovery recorded
  5. print the persisted audit trail and pipeline-health metrics

Designed to run in front of a stakeholder: colourised, paced, and it
doubles as the paper's reproducibility artifact.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import time
from typing import Any, List, Optional

import click


# ─────────────────────────────────────────────────────────────────────
# Presentation helpers
# ─────────────────────────────────────────────────────────────────────
def _step(n: int, title: str) -> None:
    click.secho(f"\n[{n}] {title}", fg="cyan", bold=True)


def _ok(msg: str) -> None:
    click.secho("    " + msg, fg="green")


def _warn(msg: str) -> None:
    click.secho("    " + msg, fg="yellow", bold=True)


def _info(msg: str) -> None:
    click.echo("    " + msg)


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _healthy_rows(n: int) -> List[dict]:
    # amount in dollars: a clean, well-behaved numeric column
    return [{"amount": float(x)} for x in range(n)]


def _unit_bug_rows(n: int) -> List[dict]:
    # the classic cents-as-dollars ingestion bug: every value x100
    return [{"amount": float(x) * 100.0} for x in range(n)]


def _as_events(payload: Any) -> list:
    if isinstance(payload, dict):
        for key in ("events", "drift_events", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return []
    return payload if isinstance(payload, list) else []


# ─────────────────────────────────────────────────────────────────────
# In-process app boot (no server needed)
# ─────────────────────────────────────────────────────────────────────
def _load_app():
    """Import the real UASR ASGI app, ensuring a writable metadata DB first.

    Raises a clear click error if the backend isn't importable so the user
    knows to fall back to --live.
    """
    # Ensure a writable metadata DB unless the operator already set one.
    if not os.environ.get("METADATA_DATABASE_URL"):
        tmpdir = tempfile.mkdtemp(prefix="uasr-demo-")
        os.environ["METADATA_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmpdir}/demo.db"

    try:
        from uasr.service import app  # noqa: WPS433 (deliberate lazy import)
    except Exception as exc:  # pragma: no cover - environment dependent
        raise click.ClickException(
            "the UASR backend ('aurabackend') is not importable in this "
            f"environment, so the in-process demo cannot start ({exc}). "
            "Start the service yourself and re-run with:  uasr demo --live http://localhost:8000"
        ) from exc

    return app


@contextlib.asynccontextmanager
async def _inprocess_transport():
    """Yield an httpx ASGI transport bound to the real app, with lifespan run.

    httpx.ASGITransport does not fire startup/shutdown events, so the DB
    tables would never be created. We enter the app's own lifespan context
    (which runs init_uasr_db and starts the repair scheduler) around the run.
    """
    import httpx

    app = _load_app()
    async with app.router.lifespan_context(app):
        yield httpx.ASGITransport(app=app)


# ─────────────────────────────────────────────────────────────────────
# The scenario
# ─────────────────────────────────────────────────────────────────────
async def _run_scenario(client, *, source: str, rows: int, pause: float) -> int:
    """Drive the full detect->heal->verify->audit path. Returns process exit code."""
    # 0. deployment posture
    _step(0, "Deployment posture")
    dep = await client.deployment()
    _info(f"state backend : {dep.state_backend} ({dep.state_store_class})")
    _info(f"repair backend: {dep.repair_backend} ({dep.repair_backend_class})")
    _info(f"recovery mode : {dep.recovery_mode}   node: {dep.node_id}")
    _pause(pause)

    # 1. baseline
    _step(1, f"Register baseline for '{source}' from {rows} healthy rows")
    base = await client.register_baseline(source, _healthy_rows(rows))
    _ok(f"baseline registered: {base.row_count} rows, columns={base.columns}, ref={base.reference_version}")
    _pause(pause)

    # 2. healthy traffic
    _step(2, "Stream healthy batches (expected: clean)")
    for i in range(1, 3):
        res = await client.ingest(source, _healthy_rows(rows), batch_id=f"healthy-{i}")
        if res.drift_detected:
            _warn(f"batch healthy-{i}: unexpected drift {res.drift_type}/{res.severity}")
        else:
            _ok(f"batch healthy-{i}: accepted clean (no drift)")
        _pause(pause * 0.5)
    _pause(pause)

    # 3. inject the unit-bug drift
    _step(3, "Inject a x100 unit-bug batch (cents mislabelled as dollars)")
    bad = await client.ingest(source, _unit_bug_rows(rows), batch_id="unitbug-1")
    if not bad.drift_detected:
        _warn("no drift detected — the injected bug slipped through (unexpected)")
        return 1
    _warn(f"DRIFT DETECTED: {bad.drift_type} / severity={bad.severity}")
    _info(f"drift event id : {bad.drift_event_id}")
    _info(f"recovery id    : {bad.recovery_id}")
    _info(f"shim deployed  : {bad.shim_deployed}")
    if bad.post_kl is not None:
        _info(f"post-repair KL : {bad.post_kl:.4f}")
    if bad.latency_seconds is not None:
        _info(f"detect->act    : {bad.latency_seconds * 1000:.1f} ms")
    if bad.gate is not None:
        g = bad.gate
        verdict = getattr(g, "allowed", None)
        _info(f"semantic gate  : {'PASS' if verdict else 'REJECT'} "
              f"(similarity={getattr(g, 'similarity', '—')}, threshold={getattr(g, 'threshold', '—')})")
    _pause(pause)

    # 4. audit trail
    _step(4, "Persisted audit trail")
    status = await client.drift_status(source, limit=10)
    events = _as_events(status)
    if not events:
        _info("(no persisted drift events returned)")
    for ev in events[:10]:
        if isinstance(ev, dict):
            _info(f"- {ev.get('detected_at', ev.get('created_at', '?'))}  "
                  f"{ev.get('drift_type', '?')}/{ev.get('severity', '?')}  "
                  f"event={str(ev.get('drift_event_id', ev.get('id', '')))[:12]}")
    _pause(pause)

    # 5. metrics
    _step(5, "Pipeline-health metrics")
    m = await client.metrics()
    md = m.model_dump()
    if md:
        for k in ("hu", "H_u", "health", "resolution", "mean_latency_seconds", "alerts"):
            if k in md:
                _info(f"{k} = {md[k]}")
        if not any(k in md for k in ("hu", "H_u", "health")):
            _info(f"metrics keys: {sorted(md)[:12]}")
    else:
        _info("(metrics endpoint returned an empty snapshot)")

    _step(6, "Sources")
    for s in await client.sources():
        flag = "baseline" if s.has_active_baseline else "no-baseline"
        _info(f"{s.source_id:<20} {flag:<12} shims={s.deployed_shims}")

    click.secho("\n✓ demo complete — detect -> gate -> heal -> verify -> audit all exercised.",
                fg="green", bold=True)
    return 0


async def _amain(live_url: Optional[str], source: str, rows: int, pause: float,
                 api_key: Optional[str]) -> int:
    from .client import AsyncUASRClient

    if live_url:
        click.secho(f"Running against live service: {live_url}", fg="magenta")
        async with AsyncUASRClient(live_url, api_key=api_key) as client:
            return await _run_scenario(client, source=source, rows=rows, pause=pause)

    click.secho("Booting the UASR service in-process (no external server)…", fg="magenta")
    async with _inprocess_transport() as transport:
        async with AsyncUASRClient("http://uasr.local", transport=transport) as client:
            return await _run_scenario(client, source=source, rows=rows, pause=pause)


def run_demo(*, live_url: Optional[str], source: str, rows: int, pause: float,
             api_key: Optional[str] = None) -> None:
    """Entry point called by the CLI `demo` command."""
    code = asyncio.run(_amain(live_url, source, rows, pause, api_key))
    if code != 0:
        raise SystemExit(code)
