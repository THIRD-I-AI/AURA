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
from typing import Any, Dict, List, Optional

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


# ── Sprint 19 — TRAIGA Federation: Merkle audit log helpers ──────────
#
# The existing per-record SHA-256 chain stays untouched on the hot
# append path (every audit_request / audit_prompt / audit_event call
# still flushes a single line + the chain digest). The Merkle tree
# is computed on demand from the day's JSONL — typically once per
# STH publication, not per append — so the hot path overhead stays
# at zero. The Merkle root over a day's record hashes is the
# cryptographic anchor that two organisations can independently
# verify: an auditor at org B holding only (record_hash, proof, STH)
# can confirm record inclusion in AURA's audit chain at org A
# without trusting either party.


def _audit_path_for_day(day: str, service_tag: Optional[str] = None) -> Path:
    """Resolve the daily JSONL path for a given UTC day and service.

    Defaults to ``AUDIT_SERVICE_TAG`` (whatever service is asking).
    Auditors / sidecar verifiers pass an explicit ``service_tag`` to
    inspect another service's log.
    """
    tag = service_tag or AUDIT_SERVICE_TAG
    return AUDIT_DIR / f"audit-{tag}-{day}.jsonl"


def read_day_record_hashes(day: str, service_tag: Optional[str] = None) -> List[str]:
    """Return the ordered list of ``record_hash`` values for a day.

    Skips blank lines + malformed records (returning what's intact —
    a tampered line that fails JSON parse should NOT block the rest
    of the day's records from being indexed). Caller can detect
    tampering by comparing this list's length against the chain
    walk in ``verify_chain``.
    """
    path = _audit_path_for_day(day, service_tag)
    if not path.exists():
        return []
    hashes: List[str] = []
    try:
        with path.open("rb") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                h = rec.get("record_hash")
                if isinstance(h, str) and h:
                    hashes.append(h)
    except OSError as exc:
        logger.warning("read_day_record_hashes failed for %s: %s", path, exc)
    return hashes


def daily_merkle_root(day: str, service_tag: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Compute today's Merkle Tree Hash over the day's record hashes.

    Returns ``{tree_size, root_hash_hex, day, service_tag}`` or
    ``None`` when the day's file doesn't exist / is empty. The
    root is the cryptographic commitment an STH publication
    references.

    Each record_hash from the JSONL is treated as the SHA-256 of
    the line's stable fields (already produced by ``_AuditWriter``).
    We re-hash it through ``merkle.leaf_hash`` to apply RFC 6962's
    leaf prefix (0x00) before constructing the tree — this is
    REQUIRED to prevent second-preimage attacks where an attacker
    finds two record_hash values whose concatenation collides with
    a third record_hash.
    """
    from .merkle import build_tree_root, leaf_hash

    hashes = read_day_record_hashes(day, service_tag)
    if not hashes:
        return None
    leaves = [leaf_hash(h.encode("utf-8")) for h in hashes]
    root = build_tree_root(leaves)
    return {
        "day": day,
        "service_tag": service_tag or AUDIT_SERVICE_TAG,
        "tree_size": len(hashes),
        "root_hash_hex": root.hex(),
    }


def inclusion_proof_for_record(
    record_hash: str,
    day: Optional[str] = None,
    service_tag: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a Merkle inclusion proof for ``record_hash`` in its day's
    tree.

    When ``day`` is None the helper searches today's file first, then
    walks backward through the last 30 daily files looking for the
    record. Returns ``None`` if the record isn't found in that window
    — caller falls back to a 404 on the HTTP surface.

    Returns ``{day, service_tag, tree_size, leaf_index, proof_hex,
    root_hash_hex}`` on success. ``proof_hex`` is a list of 64-char
    hex-encoded sibling hashes ordered from leaf-level to root-level
    (the order ``merkle.verify_inclusion`` consumes them).
    """
    from .merkle import build_tree_root, inclusion_proof, leaf_hash

    candidate_days: List[str] = []
    if day is not None:
        candidate_days.append(day)
    else:
        now = datetime.now(timezone.utc)
        for offset in range(0, 30):
            d = (now - _days(offset)).strftime("%Y%m%d")
            candidate_days.append(d)

    for d in candidate_days:
        hashes = read_day_record_hashes(d, service_tag)
        if record_hash not in hashes:
            continue
        idx = hashes.index(record_hash)
        leaves = [leaf_hash(h.encode("utf-8")) for h in hashes]
        proof = inclusion_proof(leaves, idx)
        root = build_tree_root(leaves)
        return {
            "day": d,
            "service_tag": service_tag or AUDIT_SERVICE_TAG,
            "tree_size": len(hashes),
            "leaf_index": idx,
            "proof_hex": [p.hex() for p in proof],
            "root_hash_hex": root.hex(),
        }
    return None


def _days(n: int):
    """Tiny helper — `timedelta(days=n)`. Inlined here to keep the
    audit_log module's import list narrow."""
    from datetime import timedelta
    return timedelta(days=n)
