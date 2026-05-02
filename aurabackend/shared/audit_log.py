"""
TRAIGA Immutable Audit Log
===========================
Append-only JSONL writer for every prompt/response that flows through the
stack. Designed to satisfy the Texas Responsible AI Governance Act
traceability requirements: every AI interaction is durably recorded with
a SHA-256 hash chain so any post-hoc tampering is detectable.

Design choices
--------------
* **Append-only**: every record is one ``json.dumps(...) + "\\n"`` write
  with ``fsync`` after each line. No update/delete code paths exist in
  this module — the file format is the contract.
* **Hash chain**: each record contains ``prev_hash`` (the SHA-256 of the
  previous serialised record) and ``record_hash`` (SHA-256 of the current
  record's stable fields). A verifier can replay the chain to detect any
  inserted, removed, or edited line.
* **Daily rotation** keyed on UTC date — one file per day per service,
  named ``audit-YYYYMMDD.jsonl``. Rotation is lock-free because
  per-day filenames never collide with each other.
* **Storage assumption**: the writer points at ``AURA_AUDIT_DIR`` (default
  ``/var/log/aura/audit``), which the Helm chart mounts as a
  ``ReadWriteOnce`` PVC. Mark the underlying StorageClass ``WORM`` (e.g.
  S3 Object Lock backend) for hardware-level immutability — this module
  guarantees logical immutability; the storage layer guarantees physical.

Wire-up
-------
``service_factory.create_service`` adds ``AuditLogMiddleware`` when
``AURA_AUDIT_ENABLED=true``. The middleware records request method/path
plus the response status. To capture *prompt content*, agents call
``audit_prompt(...)`` directly from inside the LLM provider — see
``shared/llm_provider.py::_CachedProvider``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("aura.shared.audit_log")

AUDIT_ENABLED = os.getenv("AURA_AUDIT_ENABLED", "false").lower() == "true"
AUDIT_DIR = Path(os.getenv("AURA_AUDIT_DIR", "/var/log/aura/audit"))
AUDIT_SERVICE_TAG = os.getenv("AURA_SERVICE_TAG", "aura")
# Truncation guard — prompts can be huge; the audit log isn't a
# replacement for prompt-caching storage, it's a compliance artifact.
MAX_FIELD_BYTES = int(os.getenv("AURA_AUDIT_MAX_FIELD_BYTES", "16384"))


# ── Single-process writer (one append at a time) ──────────────────────

class _AuditWriter:
    """Process-local append-only writer with hash chain + daily rotation."""

    def __init__(self, base_dir: Path, service_tag: str) -> None:
        self._base_dir = base_dir
        self._service_tag = service_tag
        self._lock = threading.Lock()
        self._current_day: Optional[str] = None
        self._current_path: Optional[Path] = None
        self._prev_hash: str = ""

    # ── Path resolution ───────────────────────────────────────────────

    def _path_for(self, day: str) -> Path:
        return self._base_dir / f"audit-{self._service_tag}-{day}.jsonl"

    def _rotate_if_needed(self, now: datetime) -> None:
        day = now.strftime("%Y%m%d")
        if day == self._current_day:
            return
        self._current_day = day
        self._current_path = self._path_for(day)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        # Re-seed prev_hash from the last line of today's file (if it
        # already exists from a previous process restart) so the chain
        # survives crashes without resetting to "".
        self._prev_hash = self._tail_hash(self._current_path)

    @staticmethod
    def _tail_hash(path: Path) -> str:
        if not path.exists():
            return ""
        try:
            # Files are line-oriented and small enough that reading the
            # tail is fine; for huge logs swap for a backwards seek.
            with path.open("rb") as fh:
                last = b""
                for line in fh:
                    if line.strip():
                        last = line
                if not last:
                    return ""
                rec = json.loads(last)
                return rec.get("record_hash", "")
        except Exception as exc:
            logger.warning("audit log tail read failed (%s); chain reset", exc)
            return ""

    # ── Public append ─────────────────────────────────────────────────

    def append(self, kind: str, payload: Dict[str, Any]) -> None:
        if not AUDIT_ENABLED:
            return
        now = datetime.now(timezone.utc)
        truncated_payload = {k: _truncate(v) for k, v in payload.items()}
        with self._lock:
            self._rotate_if_needed(now)
            assert self._current_path is not None  # rotate sets it

            # Stable fields hashed in defined order so the chain is
            # reproducible by any verifier (don't rely on dict ordering).
            stable_record = {
                "ts": now.isoformat(),
                "service": self._service_tag,
                "kind": kind,
                "payload": truncated_payload,
                "prev_hash": self._prev_hash,
            }
            digest = hashlib.sha256(
                json.dumps(stable_record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            full_record = {**stable_record, "record_hash": digest}

            try:
                with self._current_path.open("ab") as fh:
                    fh.write(json.dumps(full_record, separators=(",", ":")).encode("utf-8"))
                    fh.write(b"\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                self._prev_hash = digest
            except OSError as exc:
                # An audit-write failure must NOT crash the request path,
                # but we surface it loudly — TRAIGA gaps are reportable.
                logger.error("audit log append failed: %s", exc)


def _truncate(v: Any) -> Any:
    if isinstance(v, str) and len(v.encode("utf-8")) > MAX_FIELD_BYTES:
        return v.encode("utf-8")[:MAX_FIELD_BYTES].decode("utf-8", errors="ignore") + "…[truncated]"
    if isinstance(v, dict):
        return {k: _truncate(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_truncate(x) for x in v]
    return v


# ── Module-level singleton + helpers ──────────────────────────────────

_writer: Optional[_AuditWriter] = None
_writer_lock = threading.Lock()


def get_writer() -> _AuditWriter:
    global _writer
    if _writer is None:
        with _writer_lock:
            if _writer is None:
                _writer = _AuditWriter(AUDIT_DIR, AUDIT_SERVICE_TAG)
    return _writer


def audit_request(method: str, path: str, status: int, request_id: str = "", user: str = "") -> None:
    """Record one HTTP request — emitted by AuditLogMiddleware."""
    get_writer().append("request", {
        "method": method, "path": path, "status": status,
        "request_id": request_id, "user": user,
    })


def audit_prompt(provider: str, model: str, prompt: str, response: Optional[str], cached: bool) -> None:
    """Record one LLM call — emitted from the provider boundary."""
    get_writer().append("llm_call", {
        "provider": provider, "model": model, "cached": cached,
        "prompt": prompt, "response": response or "",
    })


def audit_event(kind: str, payload: Dict[str, Any]) -> None:
    """Record an arbitrary structured event (drift detection, recovery, etc.)."""
    get_writer().append(kind, payload)


# ── Verifier (used by the auditor sidecar / CLI) ──────────────────────

def verify_chain(path: Path) -> Dict[str, Any]:
    """Re-walk the file and confirm every record_hash matches its stable
    fields and chains correctly to its predecessor. Returns a report."""
    prev = ""
    line_no = 0
    failures = []
    with path.open("rb") as fh:
        for raw in fh:
            line_no += 1
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                failures.append({"line": line_no, "error": f"bad json: {exc}"})
                continue
            stable = {k: rec[k] for k in ("ts", "service", "kind", "payload", "prev_hash")}
            expected = hashlib.sha256(
                json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if rec.get("record_hash") != expected:
                failures.append({"line": line_no, "error": "record_hash mismatch"})
            if rec.get("prev_hash") != prev:
                failures.append({"line": line_no, "error": "prev_hash mismatch"})
            prev = rec.get("record_hash", "")
    return {"path": str(path), "lines": line_no, "failures": failures, "ok": not failures}
