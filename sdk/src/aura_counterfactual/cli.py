"""
Command-line interface for the Counterfactual Audit Engine.

Installed as ``aura-counterfactual`` via the ``[project.scripts]`` entry
in pyproject.toml.

Subcommands:

* ``info``           — show engine capabilities (DoWhy / signing / PDF / model lists)
* ``run``            — submit a query JSON file and block until done
* ``replay <hash>``  — fetch a sealed artifact by audit_record_hash
* ``verify <hash>``  — verify the signature of a sealed artifact
* ``public-key``     — print the engine's ED25519 public key (PEM)
* ``report <hash>``  — download the auditor PDF report

Default base URL is ``http://localhost:8000`` (API gateway). Override
with ``--base-url`` or env ``AURA_BASE_URL``. JSON output via
``--json`` for shell-pipeline use.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

import click

from .client import (
    Client,
    EngineError,
    JobFailedError,
    JobTimeoutError,
    NotFoundError,
    ServiceUnavailableError,
)

# ── Shared options ────────────────────────────────────────────────────

def _make_client(base_url: str, prefix: str, api_key: Optional[str]) -> Client:
    return Client(base_url=base_url, prefix=prefix, api_key=api_key)


def _emit(value: Any, *, as_json: bool) -> None:
    """Print either JSON (machine-readable) or a human-friendly summary."""
    if as_json:
        if hasattr(value, "model_dump"):
            payload = value.model_dump(mode="json")
        else:
            payload = value
        click.echo(json.dumps(payload, indent=2, default=str))
        return
    # Human path — handle the most common return types compactly
    if hasattr(value, "model_dump"):
        d = value.model_dump(mode="json")
    elif isinstance(value, dict):
        d = value
    else:
        click.echo(str(value))
        return
    for k, v in d.items():
        click.echo(f"{k}: {v}")


# ── CLI group ─────────────────────────────────────────────────────────

@click.group()
@click.option(
    "--base-url",
    envvar="AURA_BASE_URL",
    default="http://localhost:8000",
    show_default=True,
    help="Engine base URL. Override with AURA_BASE_URL.",
)
@click.option(
    "--prefix", default="/api/v1", show_default=True,
    help="URL prefix; use '' to talk to the standalone counterfactual_service directly.",
)
@click.option(
    "--api-key", envvar="AURA_API_KEY", default=None,
    help="Bearer token sent as Authorization: Bearer <key>. Override with AURA_API_KEY.",
)
@click.pass_context
def cli(ctx: click.Context, base_url: str, prefix: str, api_key: Optional[str]) -> None:
    """AURA Counterfactual Audit Engine CLI."""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["prefix"] = prefix
    ctx.obj["api_key"] = api_key


# ── Subcommands ───────────────────────────────────────────────────────

@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of human text.")
@click.pass_context
def info(ctx: click.Context, as_json: bool) -> None:
    """Show engine capabilities."""
    with _make_client(**ctx.obj) as c:
        _emit(c.info(), as_json=as_json)


@cli.command()
@click.argument("query_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--timeout", "timeout_s", default=180.0, show_default=True,
              help="Max seconds to wait for the job.")
@click.option("--poll", "poll_interval_s", default=1.0, show_default=True,
              help="Seconds between status polls.")
@click.option("--json", "as_json", is_flag=True, help="Emit the artifact as JSON.")
@click.option("--save", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Write the artifact JSON to this path on success.")
@click.pass_context
def run(
    ctx: click.Context,
    query_file: Path,
    timeout_s: float,
    poll_interval_s: float,
    as_json: bool,
    save: Optional[Path],
) -> None:
    """Submit a counterfactual query (loaded from a JSON file) and wait for the artifact."""
    try:
        query = json.loads(query_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        click.echo(f"Could not parse {query_file}: {exc}", err=True)
        sys.exit(1)
    with _make_client(**ctx.obj) as c:
        try:
            artifact = c.run(query, timeout_s=timeout_s, poll_interval_s=poll_interval_s)
        except JobFailedError as exc:
            click.echo(f"Job failed: {exc}", err=True)
            sys.exit(2)
        except JobTimeoutError as exc:
            click.echo(f"Job timed out: {exc}", err=True)
            sys.exit(3)
        except EngineError as exc:
            click.echo(f"Engine error: {exc}", err=True)
            sys.exit(1)
    if save:
        save.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2), encoding="utf-8")
        click.echo(f"Artifact written to {save}")
    if as_json or not save:
        _emit(artifact, as_json=as_json)


@cli.command()
@click.argument("record_hash")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of human text.")
@click.pass_context
def replay(ctx: click.Context, record_hash: str, as_json: bool) -> None:
    """Fetch a sealed artifact by its audit_record_hash."""
    with _make_client(**ctx.obj) as c:
        try:
            artifact = c.replay(record_hash)
        except NotFoundError:
            click.echo(f"Artifact {record_hash} not found.", err=True)
            sys.exit(4)
    _emit(artifact, as_json=as_json)


@cli.command()
@click.argument("record_hash")
@click.pass_context
def verify(ctx: click.Context, record_hash: str) -> None:
    """Verify the ED25519 signature of a sealed artifact."""
    with _make_client(**ctx.obj) as c:
        try:
            result = c.verify(record_hash)
        except NotFoundError:
            click.echo(f"Artifact {record_hash} not found.", err=True)
            sys.exit(4)
    if result.verified:
        click.echo(f"OK — {record_hash[:16]}… signature verified ({result.signature_status})")
    else:
        click.echo(
            f"FAIL — {record_hash[:16]}… {result.reason} ({result.signature_status})",
            err=True,
        )
        sys.exit(5)


@cli.command(name="public-key")
@click.pass_context
def public_key(ctx: click.Context) -> None:
    """Print the engine's current ED25519 public key (PEM)."""
    with _make_client(**ctx.obj) as c:
        try:
            click.echo(c.public_key_pem())
        except ServiceUnavailableError:
            click.echo("Signing unavailable on this engine deployment.", err=True)
            sys.exit(6)


@cli.command()
@click.argument("record_hash")
@click.option(
    "-o", "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output file. Defaults to ./counterfactual-<hash[:12]>.pdf",
)
@click.pass_context
def report(ctx: click.Context, record_hash: str, output: Optional[Path]) -> None:
    """Download the auditor PDF report for a sealed artifact."""
    if output is None:
        output = Path(f"counterfactual-{record_hash[:12]}.pdf")
    with _make_client(**ctx.obj) as c:
        try:
            data = c.report_pdf(record_hash)
        except NotFoundError:
            click.echo(f"Artifact {record_hash} not found.", err=True)
            sys.exit(4)
        except ServiceUnavailableError:
            click.echo(
                "PDF renderer unavailable in this engine deployment "
                "(reportlab not installed).", err=True,
            )
            sys.exit(6)
    output.write_bytes(data)
    click.echo(f"PDF written to {output} ({len(data):,} bytes)")


@cli.command(name="bulk-replay")
@click.option(
    "-f", "--hashes-file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="File with one hash per line (lines starting with # are skipped).",
)
@click.argument("hashes", nargs=-1)
@click.option("--json", "as_json", is_flag=True,
              help="Emit one NDJSON record per line (machine-readable).")
@click.pass_context
def bulk_replay(
    ctx: click.Context,
    hashes_file: Optional[Path],
    hashes: tuple,
    as_json: bool,
) -> None:
    """Stream verify results for many artifacts at once.

    Hashes may be supplied as positional arguments OR from a file via
    ``--hashes-file``. The two sources are combined; duplicates are
    deduplicated server-side. Exit code 0 if ALL hashes verify; exit
    code 5 (the same code single-shot ``verify`` uses for verify-failed)
    if ANY hash returned a non-ok status — useful for CI gates that
    sweep the audit chain.
    """
    collected: list[str] = list(hashes)
    if hashes_file is not None:
        for line in hashes_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                collected.append(line)
    if not collected:
        click.echo("No hashes supplied (pass positional args or --hashes-file).", err=True)
        sys.exit(1)

    any_bad = False
    with _make_client(**ctx.obj) as c:
        for row in c.bulk_replay(collected):
            if row.get("status") != "ok":
                any_bad = True
            if as_json:
                click.echo(json.dumps(row))
            else:
                status = row.get("status", "?")
                rh = row.get("record_hash", "?")
                short = rh[:16] + ("…" if len(rh) > 16 else "")
                if status == "ok":
                    click.echo(f"OK            {short}")
                elif status == "not_found":
                    click.echo(f"NOT_FOUND     {short}", err=True)
                elif status == "unsigned":
                    click.echo(f"UNSIGNED      {short}", err=True)
                elif status == "verify_failed":
                    click.echo(f"VERIFY_FAILED {short}", err=True)
                else:
                    reason = row.get("reason", "")
                    click.echo(f"ERROR         {short}  {reason}", err=True)
    if any_bad:
        sys.exit(5)


def main() -> None:
    """Module entry-point — used by tests + python -m aura_counterfactual.cli."""
    cli(prog_name="aura-counterfactual", obj={})


if __name__ == "__main__":
    main()
