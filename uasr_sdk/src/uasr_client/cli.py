"""
Command-line interface for the UASR self-healing service.

    uasr --url http://localhost:8000 deployment
    uasr baseline orders --file history.csv
    uasr ingest  orders --file batch.csv --batch-id b42
    uasr drift   --source orders
    uasr metrics
    uasr sources
    uasr approve <recovery_id> --approver alice --note "looks right"

Batches are read from CSV (header row -> columns) or JSON (list of objects,
or an object with ``columns``/``rows``). Numeric-looking CSV cells are cast
to float so distributions compute correctly.
"""
from __future__ import annotations

import csv
import json
import sys
from typing import Any, Dict, List

import click

from . import __version__
from .client import UASRAPIError, UASRClient, UASRConnectionError


# ─────────────────────────────────────────────────────────────────────
# Row loading
# ─────────────────────────────────────────────────────────────────────
def _maybe_num(v: str) -> Any:
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in v and "e" not in v.lower() else f
    except (ValueError, AttributeError):
        return v


def _load_rows(path: str) -> List[Dict[str, Any]]:
    if path == "-":
        text = sys.stdin.read()
        data = json.loads(text)
        return _coerce_json(data)
    if path.lower().endswith(".json"):
        with open(path) as fh:
            return _coerce_json(json.load(fh))
    # default: CSV
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        return [{k: _maybe_num(v) for k, v in row.items()} for row in reader]


def _coerce_json(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    if isinstance(data, list):
        return data
    raise click.ClickException("JSON must be a list of objects or an object with a 'rows' key")


def _echo_json(obj: Any) -> None:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    click.echo(json.dumps(obj, indent=2, default=str))


def _run(fn):
    """Wrap a client call: translate SDK errors into clean CLI failures."""
    try:
        return fn()
    except UASRConnectionError as e:
        raise click.ClickException(f"connection failed: {e}") from e
    except UASRAPIError as e:
        raise click.ClickException(f"API error {e.status_code}: {e.detail}") from e


# ─────────────────────────────────────────────────────────────────────
# CLI group
# ─────────────────────────────────────────────────────────────────────
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--url", default="http://localhost:8000", envvar="UASR_URL",
              help="Base URL of the UASR service (env: UASR_URL).", show_default=True)
@click.option("--api-key", default=None, envvar="UASR_API_KEY", help="Bearer token (env: UASR_API_KEY).")
@click.option("--timeout", default=30.0, help="Request timeout (seconds).", show_default=True)
@click.version_option(__version__, prog_name="uasr")
@click.pass_context
def cli(ctx: click.Context, url: str, api_key: str, timeout: float) -> None:
    """Operate the AURA UASR self-healing layer from the command line."""
    ctx.obj = UASRClient(url, api_key=api_key, timeout=timeout)


@cli.command()
@click.pass_obj
def deployment(client: UASRClient) -> None:
    """Show the service's deployment configuration (backends, node)."""
    info = _run(client.deployment)
    click.echo(f"state backend : {info.state_backend} ({info.state_store_class})")
    click.echo(f"repair backend: {info.repair_backend} ({info.repair_backend_class})")
    click.echo(f"MAPE-K        : {'on' if info.mapek_enabled else 'off'}")
    click.echo(f"recovery mode : {info.recovery_mode}")
    click.echo(f"node id       : {info.node_id}")


@cli.command()
@click.argument("source_id")
@click.option("--file", "-f", "path", required=True, help="CSV/JSON of baseline rows ('-' for stdin JSON).")
@click.pass_obj
def baseline(client: UASRClient, source_id: str, path: str) -> None:
    """Register the reference baseline for SOURCE_ID from a file."""
    rows = _load_rows(path)
    res = _run(lambda: client.register_baseline(source_id, rows))
    click.echo(f"registered baseline for '{res.source_id}': "
               f"{res.row_count} rows, {len(res.columns)} cols, ref={res.reference_version}")


@cli.command()
@click.argument("source_id")
@click.option("--file", "-f", "path", required=True, help="CSV/JSON of batch rows ('-' for stdin JSON).")
@click.option("--batch-id", default="", help="Optional batch identifier.")
@click.pass_obj
def ingest(client: UASRClient, source_id: str, path: str, batch_id: str) -> None:
    """Push a batch through detect -> gate -> heal for SOURCE_ID."""
    rows = _load_rows(path)
    res = _run(lambda: client.ingest(source_id, rows, batch_id=batch_id))
    if not res.drift_detected:
        click.secho(f"clean: {len(rows)} rows accepted (batch {res.batch_id or batch_id})", fg="green")
        return
    click.secho(f"DRIFT: {res.drift_type} / {res.severity}", fg="yellow", bold=True)
    click.echo(f"  drift event : {res.drift_event_id}")
    click.echo(f"  recovery    : {res.recovery_id}")
    click.echo(f"  shim deployed: {res.shim_deployed}")
    if res.post_kl is not None:
        click.echo(f"  post-repair KL: {res.post_kl:.4f}")
    if res.latency_seconds is not None:
        click.echo(f"  latency     : {res.latency_seconds * 1000:.1f} ms")


@cli.command()
@click.option("--source", default=None, help="Filter by source id.")
@click.option("--limit", default=20, show_default=True)
@click.pass_obj
def drift(client: UASRClient, source: str, limit: int) -> None:
    """List recent drift events."""
    _echo_json(_run(lambda: client.drift_status(source, limit=limit)))


@cli.command()
@click.option("--window", type=float, default=None, help="Rolling window in seconds.")
@click.pass_obj
def metrics(client: UASRClient, window: float) -> None:
    """Show the current pipeline-health metrics (Hᵤ, resolution, latency)."""
    _echo_json(_run(lambda: client.metrics(window_seconds=window)))


@cli.command()
@click.pass_obj
def sources(client: UASRClient) -> None:
    """List all registered sources and their shim/baseline state."""
    for s in _run(client.sources):
        flag = "baseline" if s.has_active_baseline else "no-baseline"
        click.echo(f"{s.source_id:<24} {flag:<12} shims={s.deployed_shims}")


@cli.command()
@click.argument("recovery_id")
@click.option("--approver", required=True)
@click.option("--note", default=None)
@click.pass_obj
def approve(client: UASRClient, recovery_id: str, approver: str, note: str) -> None:
    """Approve a held recovery."""
    _echo_json(_run(lambda: client.approve(recovery_id, approver=approver, note=note)))


@cli.command()
@click.argument("recovery_id")
@click.option("--approver", required=True)
@click.option("--reason", required=True)
@click.pass_obj
def reject(client: UASRClient, recovery_id: str, approver: str, reason: str) -> None:
    """Reject a held recovery (escalates it)."""
    _echo_json(_run(lambda: client.reject(recovery_id, approver=approver, reason=reason)))


@cli.command()
@click.argument("source_id")
@click.pass_obj
def rollback(client: UASRClient, source_id: str) -> None:
    """Roll back the most recent shim for SOURCE_ID."""
    _echo_json(_run(lambda: client.rollback(source_id)))


@cli.command()
@click.option("--live", "live_url", default=None,
              help="Run against an already-running service at this URL "
                   "(default: boot the service in-process, no server needed).")
@click.option("--source", default="demo_orders", show_default=True, help="Source id to use.")
@click.option("--rows", default=500, show_default=True, help="Rows per batch.")
@click.option("--pause", default=0.8, show_default=True,
              help="Seconds to pause between steps (0 for no pacing).")
@click.pass_context
def demo(ctx: click.Context, live_url: str, source: str, rows: int, pause: float) -> None:
    """Run the end-to-end self-healing demo (detect -> heal -> verify -> audit).

    With no options it boots the real UASR service in-process and needs no
    external server. Pass --live URL to drive a running deployment instead.
    """
    from .demo import run_demo

    # Reuse the group's --url as the live target if the user set it explicitly.
    if live_url is None and ctx.parent is not None:
        supplied = ctx.parent.params.get("url")
        if supplied and supplied != "http://localhost:8000":
            live_url = supplied
    api_key = ctx.parent.params.get("api_key") if ctx.parent else None
    run_demo(live_url=live_url, source=source, rows=rows, pause=pause, api_key=api_key)


if __name__ == "__main__":
    cli()
