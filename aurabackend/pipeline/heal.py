"""The heal step of a batch pipeline run (ADR-002 phase 2b).

After a source table is loaded into the run's DuckDB connection, and before any
transform reads it, every row goes through UASR ``/uasr/heal`` as the pipeline's
tenant. Healed rows replace the table's rows; quarantined rows are moved to a staging
table the engine writes to the pipeline's quarantine table, and never reach a sink.

The rules this module keeps (docs/design/ADR-002, "Phase 2b contract"):

* fail closed -- a call that cannot be made, answers an error, or answers rows that
  do not account for the page raises ``HealUnavailable``; the run is FAILED and the
  sink is never written, because writing rows nobody checked would be a guess;
* every source row ends up exactly once in the healed table or in quarantine;
* memory is bounded: the table is read one page at a time, never whole into Python;
* the first run of a source registers its baseline (``only_if_absent``) and is not
  healed against itself;
* the run's last call for a source carries ``metadata.complete_run`` and the run's
  row count, so the volume check judges runs, never pages;
* clean data flows through untouched: a page UASR did not rewrite keeps its
  original DuckDB rows and types (less any quarantined rows), and is not rebuilt
  from JSON.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from pipeline.models import PipelineHealSummary, SourceHealSummary
from shared.sql_identifiers import quote_identifier as _q

logger = logging.getLogger("aura.pipeline.heal")

# A page is at most this many rows and this many cells. 2000 rows is inside the batch
# sizes the ADR-002 benchmark measured (20-4000 rows, 0 false alarms); the cell bound
# keeps a wide table's page, its JSON and UASR's copy of it small on the 1 GB box.
PAGE_MAX_ROWS = 2000
PAGE_MAX_CELLS = 200_000
_TIMEOUT_SECONDS = 120.0
_MAX_ALERTS_KEPT = 50

QUARANTINE_STAGE = "__aura_quarantine_stage"

# DuckDB types whose Python value JSON carries exactly. Every other type goes to UASR
# as DuckDB's own text for the value, which CAST restores exactly (DATE, TIMESTAMP,
# UUID, BLOB, HUGEINT, nested types, ...).
_JSON_NATIVE = {
    "BOOLEAN", "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "UTINYINT", "USMALLINT",
    "UINTEGER", "FLOAT", "DOUBLE", "VARCHAR",
}
_DECIMAL = re.compile(r"^DECIMAL\((\d+),\s*(\d+)\)$")


class HealUnavailable(ConnectionError):
    """UASR could not check a load. The run fails and nothing reaches a sink (ADR-002)."""


def _send_expr(name: str, dtype: str) -> str:
    """How a column is read for UASR.

    A DECIMAL of at most 15 digits goes as a DOUBLE: a double holds 15 significant
    digits exactly, so CAST back to DECIMAL(p,s) restores the value, and UASR sees a
    number it can check for a unit fault instead of opaque text.
    """
    if dtype in _JSON_NATIVE:
        return _q(name)
    m = _DECIMAL.match(dtype)
    if m and int(m.group(1)) <= 15:
        return f"CAST({_q(name)} AS DOUBLE) AS {_q(name)}"
    return f"CAST({_q(name)} AS VARCHAR) AS {_q(name)}"


def _ordered_columns(rows: List[Dict[str, Any]]) -> List[str]:
    seen: Dict[str, None] = {}
    for r in rows:
        for k in r:
            seen.setdefault(k, None)
    return list(seen)


class RunHealer:
    """Heals the source tables of one pipeline run, on the run's connection."""

    def __init__(self, conn: Any, *, base_url: str, headers: Dict[str, str],
                 pipeline_id: str, run_id: str) -> None:
        self.conn = conn
        self.base_url = base_url.rstrip("/")
        self.headers = headers
        self.pipeline_id = pipeline_id
        self.run_id = run_id
        self.sources: List[SourceHealSummary] = []
        self.quarantined = 0
        self._stage_ready = False
        # Set by the engine once the quarantined rows are written to the pipeline's table.
        self.quarantine_table: Optional[str] = None
        # UASR's own "degraded": e.g. a standing shim failed to apply to a page.
        self._uasr_degraded = False

    # ── public ─────────────────────────────────────────────────────────

    def source_id(self, source: str) -> str:
        # UASR places the id inside the caller's tenant (uasr/tenancy.py), so two
        # tenants' pipelines never share a baseline; the "pipeline:" prefix keeps an id
        # a client chose from ever reading as another tenant's "<tenant>::" namespace.
        return f"pipeline:{_safe(self.pipeline_id)}:{source}"

    def summary(self) -> PipelineHealSummary:
        return PipelineHealSummary(
            sources=self.sources,
            rows_quarantined=self.quarantined,
            repairs=sum(len(s.repairs) for s in self.sources),
            alerts=sum(len(s.alerts) for s in self.sources),
            quarantine_table=self.quarantine_table,
        )

    @property
    def degraded(self) -> bool:
        return any(s.rows_quarantined or s.alerts for s in self.sources) or self._uasr_degraded

    async def heal_table(self, table: str, source: str) -> SourceHealSummary:
        columns, total = await asyncio.to_thread(self._describe, table)
        source_id = self.source_id(source)
        summary = SourceHealSummary(source=source, source_id=source_id, mode="healed", rows_in=total)
        self.sources.append(summary)
        if total == 0:
            # Nothing can reach a sink from an empty load, and an empty page could
            # neither register a baseline nor be compared with one.
            summary.mode = "empty"
            return summary

        page_rows = max(1, min(PAGE_MAX_ROWS, PAGE_MAX_CELLS // max(1, len(columns))))
        pages = math.ceil(total / page_rows)
        complete = {"complete_run": True, "run_rows": total}
        # Pages whose rows UASR changed (a repair or a standing shim) are rebuilt from
        # its answer. A page that only lost quarantined rows keeps its original rows.
        rewritten: Set[int] = set()
        page_map = await asyncio.to_thread(self._build_page_map, table, page_rows)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, headers=self.headers) as client:
                first = await asyncio.to_thread(self._fetch_page, table, page_map, columns, 0)
                base = await self._post(client, "/uasr/baseline", {
                    "source_id": source_id, "columns": [c for c, _ in columns], "rows": first[1],
                    # BUG-563: a baseline that exists is kept; only a source's first run seeds one.
                    "only_if_absent": True, "metadata": complete,
                }, source)
                if base.get("status") == "registered":
                    # The first clean load is the baseline; healing it against itself
                    # would prove nothing, so its rows pass as loaded.
                    summary.mode = "baseline_registered"
                    summary.rows_out = total
                    return summary
                if base.get("status") != "exists":
                    raise HealUnavailable(
                        f"UASR could not register or confirm the baseline of source '{source}'; "
                        "no rows were written")

                for k in range(pages):
                    rowids, rows = first if k == 0 else await asyncio.to_thread(
                        self._fetch_page, table, page_map, columns, k)
                    meta = {"pipeline_id": self.pipeline_id, "run_id": self.run_id}
                    if k == pages - 1:
                        meta.update(complete)
                    out = await self._post(client, "/uasr/heal", {
                        "source_id": source_id, "batch_id": f"{self.run_id}:{source}:{k}",
                        "columns": [c for c, _ in columns], "rows": rows, "metadata": meta,
                    }, source)
                    held = _check_answer(out, len(rows), source)
                    summary.pages += 1
                    self._record(summary, out)
                    if held:
                        await asyncio.to_thread(
                            self._stage_quarantine, source, rowids, rows, out["quarantined_rows"])
                        summary.rows_quarantined += len(held)
                        self.quarantined += len(held)
                    if out.get("repairs") or out.get("shims_applied"):
                        rewritten.add(k)
                        kept_ids = [rid for i, rid in enumerate(rowids) if i not in held]
                        await asyncio.to_thread(
                            self._stage_page, table, columns, k, kept_ids, out.get("rows") or [],
                            out.get("repairs") or [])
                    # Only one page is ever held in Python (memory bound, ADR-002).
                    first = None
                    del rows, rowids, out

            if rewritten or summary.rows_quarantined:
                await asyncio.to_thread(self._rebuild, table, page_map, pages, source, rewritten)
        finally:
            await asyncio.to_thread(self._drop, page_map)
        summary.rows_out = total - summary.rows_quarantined
        return summary

    # ── UASR ───────────────────────────────────────────────────────────

    async def _post(self, client: Any, path: str, payload: Dict[str, Any], source: str) -> Dict[str, Any]:
        body = await asyncio.to_thread(lambda: json.dumps(payload).encode("utf-8"))
        try:
            resp = await client.post(f"{self.base_url}{path}", content=body,
                                     headers={"Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            raise HealUnavailable(
                f"UASR could not be reached to check source '{source}' ({type(exc).__name__}); "
                "no rows were written") from None
        if resp.status_code >= 400:
            raise HealUnavailable(
                f"UASR answered HTTP {resp.status_code} for source '{source}'; no rows were written")
        try:
            out = await asyncio.to_thread(json.loads, resp.content)
        except ValueError:
            raise HealUnavailable(
                f"UASR answered a non-JSON body for source '{source}'; no rows were written") from None
        if not isinstance(out, dict):
            raise HealUnavailable(f"UASR answered an unexpected body for source '{source}'; no rows were written")
        return out

    def _record(self, summary: SourceHealSummary, out: Dict[str, Any]) -> None:
        for r in out.get("repairs") or []:
            key = (r.get("type"), r.get("column"))
            if not any((x.get("type"), x.get("column")) == key for x in summary.repairs):
                summary.repairs.append(r)
        for a in out.get("alerts") or []:
            if len(summary.alerts) < _MAX_ALERTS_KEPT:
                summary.alerts.append(a)
        if out.get("degraded"):
            self._uasr_degraded = True

    # ── DuckDB (each runs in a worker thread) ─────────────────────────

    def _describe(self, table: str) -> Tuple[List[Tuple[str, str]], int]:
        columns = [(r[0], r[1]) for r in self.conn.execute(f"DESCRIBE {_q(table)}").fetchall()]
        total = self.conn.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0]
        return columns, int(total)

    def _build_page_map(self, table: str, page_rows: int) -> str:
        """Assign every row of ``table`` to a page, in a pseudo-random but fixed order.

        Pages are slices of the rows ordered by a hash of their rowid, so each page is
        a sample of the whole load. A contiguous slice of a source sorted by date or id
        holds one end of the range, and a strided one (rowid % pages) aliases with any
        periodic pattern in the rows (alternating categories): either way a page of
        clean data would drift from the baseline, a false alarm ADR-002 forbids. The
        order is deterministic, so the first run's baseline page is a stable sample.
        The map holds two integers per row and lives in DuckDB, not in Python.
        """
        page_map = f"__aura_pages_{table}"
        self.conn.execute(
            f"CREATE TABLE {_q(page_map)} AS SELECT rowid AS rid, "
            f"(row_number() OVER (ORDER BY hash(rowid), rowid) - 1) // {int(page_rows)} AS page "
            f"FROM {_q(table)}")
        return page_map

    def _drop(self, table: str) -> None:
        self.conn.execute(f"DROP TABLE IF EXISTS {_q(table)}")

    def _fetch_page(self, table: str, page_map: str, columns: List[Tuple[str, str]], k: int
                    ) -> Tuple[List[int], List[Dict[str, Any]]]:
        """Page ``k`` of the page map: its rowids and its rows as sent to UASR."""
        names = [c for c, _ in columns]
        select = ", ".join(_send_expr(c, t) for c, t in columns)
        cur = self.conn.execute(
            f"SELECT rowid, {select} FROM {_q(table)} "
            f"WHERE rowid IN (SELECT rid FROM {_q(page_map)} WHERE page = {int(k)}) ORDER BY rowid")
        rowids: List[int] = []
        rows: List[Dict[str, Any]] = []
        for rec in cur.fetchall():
            rowids.append(int(rec[0]))
            rows.append(dict(zip(names, rec[1:])))
        return rowids, rows

    def _ensure_stage(self) -> None:
        if not self._stage_ready:
            self.conn.execute(
                f"CREATE TABLE {QUARANTINE_STAGE} (run_id VARCHAR, source VARCHAR, row_index BIGINT, "
                "reason VARCHAR, original_row VARCHAR, quarantined_at TIMESTAMP)")
            self._stage_ready = True

    def _stage_quarantine(self, source: str, rowids: List[int], rows: List[Dict[str, Any]],
                          quarantined: List[Dict[str, Any]]) -> None:
        self._ensure_stage()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # The row as the source delivered it (as read for UASR), not UASR's echo of it.
        self.conn.executemany(
            f"INSERT INTO {QUARANTINE_STAGE} VALUES (?, ?, ?, ?, ?, ?)",
            [[self.run_id, source, rowids[q["index"]], str(q.get("reason") or "quarantined"),
              json.dumps(rows[q["index"]], default=str), now] for q in quarantined])

    def _stage_page(self, table: str, columns: List[Tuple[str, str]], k: int, rowids: List[int],
                    rows: List[Dict[str, Any]], repairs: List[Dict[str, Any]]) -> None:
        """Write page ``k``'s healed rows to their own table, typed like the source.

        JSON gave each value back as a number, string or bool, so a column is cast
        back to the source's type -- through its rename, if UASR renamed it -- except a
        column a retype or rescale repaired: there the healed value's own type is the
        correct one (the source's VARCHAR or DOUBLE was the fault).
        """
        from pipeline.engine import _typed_arrow_table

        stage = _page_table(table, k)
        if not rows:
            return
        source_types = dict(columns)
        renamed_from = {r.get("column"): (r.get("proof") or {}).get("from")
                        for r in repairs if r.get("type") == "rename"}
        value_repaired = {r.get("column") for r in repairs if r.get("type") in ("retype", "rescale")}
        import pyarrow as pa

        names = _ordered_columns(rows)
        arrow = _typed_arrow_table(names, rows).append_column(
            "__aura_row", pa.array(rowids, type=pa.int64()))
        view = f"__aura_page_rows_{k}"
        self.conn.register(view, arrow)
        try:
            healed_types = {r[0]: r[1] for r in self.conn.execute(f"DESCRIBE {_q(view)}").fetchall()}
            exprs = []
            for c in names:
                want = None if c in value_repaired else source_types.get(renamed_from.get(c) or c)
                if want and healed_types.get(c) != want:
                    exprs.append(f"CAST({_q(c)} AS {want}) AS {_q(c)}")
                else:
                    exprs.append(_q(c))
            self.conn.execute(
                f"CREATE TABLE {_q(stage)} AS SELECT __aura_row, {', '.join(exprs)} FROM {_q(view)}")
        finally:
            self.conn.unregister(view)

    def _rebuild(self, table: str, page_map: str, pages: int, source: str, rewritten: Set[int]) -> None:
        """Replace ``table`` with its healed rows, in source order.

        A page UASR rewrote comes from its staged table. Every other page keeps its
        original DuckDB rows, values and types untouched, minus the rows this run
        quarantined from it (read back from the quarantine stage, not held in Python).
        """
        keep = [k for k in range(pages) if k not in rewritten]
        parts = []
        params: Dict[str, Any] = {}
        if keep:
            held = ""
            if self._stage_ready:
                held = (f" AND rowid NOT IN (SELECT row_index FROM {QUARANTINE_STAGE} "
                        "WHERE run_id = $run AND source = $source)")
                params = {"run": self.run_id, "source": source}
            parts.append(
                f"SELECT rowid AS __aura_row, * FROM {_q(table)} WHERE rowid IN "
                f"(SELECT rid FROM {_q(page_map)} WHERE page IN ({', '.join(str(k) for k in keep)})){held}")
        existing = {r[0] for r in self.conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        parts += [f"SELECT * FROM {_q(_page_table(table, k))}" for k in sorted(rewritten)
                  if _page_table(table, k) in existing]
        healed = f"__aura_healed_{table}"
        if parts:
            self.conn.execute(
                f"CREATE TABLE {_q(healed)} AS SELECT * EXCLUDE (__aura_row) FROM ("
                + " UNION ALL BY NAME ".join(parts) + ") ORDER BY __aura_row", params)
        else:
            # Every row was quarantined: the transforms see the source's columns, empty.
            self.conn.execute(f"CREATE TABLE {_q(healed)} AS SELECT * FROM {_q(table)} LIMIT 0")
        for k in rewritten:
            self.conn.execute(f"DROP TABLE IF EXISTS {_q(_page_table(table, k))}")
        self.conn.execute(f"DROP TABLE {_q(table)}")
        self.conn.execute(f"ALTER TABLE {_q(healed)} RENAME TO {_q(table)}")


def _page_table(table: str, k: int) -> str:
    return f"__aura_healed_{table}_p{k}"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name)) or "pipeline"


def _check_answer(out: Dict[str, Any], sent: int, source: str) -> Set[int]:
    """The quarantined indices of a heal answer that may be written, or HealUnavailable.

    ADR-002: rows plus quarantined_rows hold every row sent exactly once. An answer
    that breaks this cannot be mapped back onto the source's rows, so nothing from
    the load may be written -- fail closed rather than guess which row is which.

    Returned rows must also be checked rows. ``healed`` is false with rows returned
    only for a detection-only alert (the rows are as sent and correct; the run is
    DEGRADED) or when a deployed shim failed part-way (BUG-271: the rows may be only
    partly transformed). The second is an unchecked row, which must never reach a
    sink, so it fails the run like an unreachable UASR does.
    """
    rows = out.get("rows")
    quarantined = out.get("quarantined_rows")
    if not isinstance(rows, list) or not isinstance(quarantined, list):
        raise HealUnavailable(f"UASR's heal answer for source '{source}' had no rows; no rows were written")
    idx = [q.get("index") if isinstance(q, dict) else None for q in quarantined]
    held = set(i for i in idx if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < sent)
    if len(held) != len(idx) or len(rows) + len(held) != sent:
        raise HealUnavailable(
            f"UASR's heal answer for source '{source}' did not account for every row sent "
            f"({len(rows)} returned + {len(idx)} quarantined != {sent}); no rows were written")
    standing_failed = int(out.get("shims_applied") or 0) < int(out.get("standing_shims") or 0)
    if rows and out.get("healed") is not True and (standing_failed or not out.get("alerts")):
        raise HealUnavailable(
            f"UASR returned rows of source '{source}' it did not prove "
            f"({out.get('reason') or 'not healed'}); no rows were written")
    return held
