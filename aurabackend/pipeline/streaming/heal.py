"""The heal step of a streaming pipeline (ADR-002 phase 2b).

Every micro-batch goes through UASR ``/uasr/heal`` before any transform, window or
sink sees it. The engine (streaming_engine.py) decides when a micro-batch is sent and
what happens when the call fails; this module makes the call, checks the answer and
keeps the quarantine:

* ``MicroBatchHealer`` calls UASR over HTTP as the pipeline's tenant (the BUG-562
  mechanism, ``shared.auth.tenant_service_headers``) with the source id
  ``pipeline:<pipeline_id>:<source>``. Until the source has a baseline, the stream's
  first rows register one (``only_if_absent``) and pass unchanged: a first load is
  not healed against itself. ``complete_run`` is never sent: a stream has no whole
  run, so the volume check would judge one micro-batch as if it were a run.
* Any answer that is not a well-formed heal of exactly the rows sent raises
  ``StreamHealError``. The engine then holds the micro-batch (fail closed: ADR-002
  "writing unchecked data would be a guess").
* ``QuarantineStore`` keeps the rows UASR held aside in the per-pipeline DuckDB table
  ``_aura_quarantine_<pipeline_id>`` -- the batch pipelines' table design -- in a
  file under the tenant's own streaming-output directory (path_confinement.py).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from pipeline.streaming.models import StreamEvent, StreamPipeline
from pipeline.streaming.path_confinement import tenant_output_dir
from shared.auth import tenant_service_headers
from shared.config import settings
from shared.exceptions import AuraError
from shared.sql_identifiers import quote_identifier

_TIMEOUT_SECONDS = 30.0
QUARANTINE_FILE = "_aura_quarantine.duckdb"

# One lock per quarantine file. A stop or failure sweep can run while a micro-batch's
# write is still in its thread (asyncio.to_thread cannot be cancelled); unserialised,
# the two raced DuckDB's CREATE TABLE (catalog write-write conflict) and the sweep's
# read-then-insert, so an event landed twice (ADR-002: accounted for exactly once).
_FILE_LOCKS: Dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _file_lock(path: str) -> threading.Lock:
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(os.path.abspath(path), threading.Lock())


class StreamHealError(AuraError):
    """The micro-batch could not be checked, or the check found something to report.

    Shown to the client through StreamMetrics.errors (sanitize_error trusts AuraError
    messages), so it never carries an upstream error body -- chain the cause instead."""

    error_code = "STREAM_HEAL_ERROR"


@dataclass
class HealOutcome:
    """What one micro-batch became. ``events`` and ``quarantined`` together hold every
    event sent exactly once; ``events`` keep the order they arrived in."""

    events: List[StreamEvent]
    quarantined: List[Tuple[StreamEvent, str]] = field(default_factory=list)
    repairs: List[Dict[str, Any]] = field(default_factory=list)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    reason: Optional[str] = None
    baseline_registered: bool = False


def source_id_for(pipeline: StreamPipeline) -> str:
    return f"pipeline:{pipeline.id}:{pipeline.source.type.value}"


def _columns(rows: List[Dict[str, Any]]) -> List[str]:
    # UASR takes the first row's keys when no columns are sent; a stream's events need
    # not all carry the same keys, so send every key seen, in first-seen order.
    seen: Dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


class MicroBatchHealer:
    def __init__(self, pipeline: StreamPipeline, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.pipeline = pipeline
        self.source_id = source_id_for(pipeline)
        self.needs_baseline = True
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._client is None:
            # One client per engine run, not per micro-batch: a tick every 0.5 s would
            # otherwise open a new connection to UASR each time.
            self._client = httpx.AsyncClient(
                base_url=settings.uasr_url, timeout=_TIMEOUT_SECONDS, transport=self._transport)
        # Minted per call: the token lives 5 minutes and a stream runs for days.
        headers = tenant_service_headers(self.pipeline.tenant_id, "streaming")
        try:
            resp = await self._client.post(path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise StreamHealError(f"UASR {path} unreachable ({type(exc).__name__})") from exc
        except (TypeError, ValueError) as exc:
            # httpx encodes the body before sending; an event value JSON cannot carry.
            raise StreamHealError(f"micro-batch could not be sent to UASR {path}: not JSON-serialisable") from exc
        if resp.status_code != 200:
            raise StreamHealError(f"UASR {path} returned HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise StreamHealError(f"UASR {path} returned a non-JSON response") from exc
        if not isinstance(body, dict):
            raise StreamHealError(f"UASR {path} returned an unexpected response")
        return body

    async def heal(self, events: List[StreamEvent]) -> HealOutcome:
        rows = [ev.data for ev in events]
        columns = _columns(rows)
        if self.needs_baseline:
            body = await self._post("/uasr/baseline", {
                "source_id": self.source_id, "columns": columns, "rows": rows, "only_if_absent": True,
            })
            status = body.get("status")
            if status not in ("registered", "exists"):
                raise StreamHealError("UASR /uasr/baseline returned an unexpected response")
            self.needs_baseline = False
            if status == "registered":
                return HealOutcome(events=list(events), baseline_registered=True)
        body = await self._post("/uasr/heal", {"source_id": self.source_id, "columns": columns, "rows": rows})
        return _outcome(events, body)


def _outcome(events: List[StreamEvent], body: Dict[str, Any]) -> HealOutcome:
    """Map UASR's answer back onto the events sent, or raise if it does not account
    for every one of them exactly once (ADR-002: rows + quarantined_rows = sent)."""
    rows, held = body.get("rows"), body.get("quarantined_rows")
    if not isinstance(rows, list) or not isinstance(held, list):
        raise StreamHealError("UASR /uasr/heal returned no rows to account for the micro-batch")
    n = len(events)
    reasons: Dict[int, str] = {}
    for item in held:
        index = item.get("index") if isinstance(item, dict) else None
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < n or index in reasons:
            raise StreamHealError("UASR /uasr/heal quarantined a row it was not sent")
        reasons[index] = str(item.get("reason") or "quarantined by UASR")
    if len(rows) + len(reasons) != n or not all(isinstance(r, dict) for r in rows):
        raise StreamHealError(
            f"UASR /uasr/heal accounted for {len(rows) + len(reasons)} of {n} rows sent")
    healed = iter(rows)
    out: List[StreamEvent] = []
    quarantined: List[Tuple[StreamEvent, str]] = []
    for i, ev in enumerate(events):
        if i in reasons:
            quarantined.append((ev, reasons[i]))
        else:
            # Healed rows replace the source rows; the event's time and key stay as read.
            out.append(ev.model_copy(update={"data": next(healed)}))
    return HealOutcome(
        events=out,
        quarantined=quarantined,
        repairs=list(body.get("repairs") or []),
        alerts=list(body.get("alerts") or []),
        degraded=bool(body.get("degraded")),
        reason=body.get("reason"),
    )


class QuarantineStore:
    """``_aura_quarantine_<pipeline_id>`` in a DuckDB file in the tenant's own
    streaming-output directory, one row per quarantined event with its reason."""

    def __init__(self, pipeline: StreamPipeline, run_id: str):
        self.pipeline = pipeline
        self.run_id = run_id
        self.source_id = source_id_for(pipeline)
        self.path = os.path.join(tenant_output_dir(pipeline.tenant_id, pipeline.id), QUARANTINE_FILE)
        self.table = f"_aura_quarantine_{pipeline.id}"

    async def write(self, batch_id: str, quarantined: List[Tuple[StreamEvent, str]], *,
                    skip_written: bool = False) -> int:
        """``skip_written`` leaves out events this run has already quarantined. The
        engine's stop/failure sweep needs it: the held micro-batch it sweeps may have
        been written just before the stop, and writing it again would count its rows
        twice (ADR-002: every row accounted for exactly once). The engine waits for an
        in-flight write before sweeping, and the per-file lock serialises the rest."""
        if not quarantined:
            return 0
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        records = [
            (self.run_id, batch_id, self.source_id, ev.event_id, ev.timestamp, reason,
             json.dumps(ev.data, default=str), now)
            for ev, reason in quarantined
        ]
        # backend.md: DuckDB is blocking; never on the event loop.
        return await asyncio.to_thread(self._write_sync, records, skip_written)

    def _write_sync(self, records: List[Tuple[Any, ...]], skip_written: bool = False) -> int:
        import duckdb

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        table = quote_identifier(self.table)
        with _file_lock(self.path):
            con = duckdb.connect(self.path)
            try:
                # One transaction: DuckDB autocommits each executemany row, so a write
                # that failed part way left rows behind, and the retry of the whole
                # batch quarantined them a second time.
                con.begin()
                try:
                    con.execute(
                        f"CREATE TABLE IF NOT EXISTS {table} ("
                        "run_id VARCHAR, batch_id VARCHAR, source_id VARCHAR, event_id VARCHAR, "
                        "event_time DOUBLE, reason VARCHAR, row_json VARCHAR, quarantined_at TIMESTAMP)"
                    )
                    if skip_written:
                        written = {row[0] for row in con.execute(
                            f"SELECT event_id FROM {table} WHERE run_id = ?", [self.run_id]).fetchall()}
                        records = [r for r in records if r[3] not in written]
                    if records:
                        con.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?, ?)", records)
                    con.commit()
                except BaseException:
                    con.rollback()
                    raise
                return len(records)
            finally:
                con.close()
