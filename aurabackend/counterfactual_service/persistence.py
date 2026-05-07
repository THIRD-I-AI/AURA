"""
Counterfactual artifact persistence.

The audit log already records *events* with chain integrity. This module
handles the *content* — the full ``CounterfactualArtifact`` JSON,
content-addressed by ``record_hash``.

Why a separate store rather than embedding artifacts inline in the audit
chain:

* Audit chain entries are read sequentially by the verifier cronjob; a
  50 KB artifact body per entry blows up that read tail-cost.
* Artifacts are content-addressed; the chain is event-addressed. They
  are queried with very different access patterns.
* Replay must return byte-identical content. Storing the canonical-JSON
  bytes verbatim under ``<record_hash>.json`` is the simplest possible
  reproducibility contract.

Storage layout (all under ``AURA_ARTIFACT_DIR``, default
``/var/log/aura/artifacts``):

  artifacts/
      <record_hash>.json   # canonical-JSON bytes of the artifact
      <record_hash>.sig    # base64-encoded ED25519 signature (Sprint 9)

The ``.json`` file MUST be the canonical-JSON bytes that
``audit_record_hash`` was computed over. Any whitespace drift between
write-time and read-time would break replay.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .canonical import canonical_dumps

logger = logging.getLogger("aura.counterfactual.persistence")


def artifact_dir() -> Path:
    """Return the configured artifact directory, creating it if needed.

    Resolution order:
      1. ``AURA_ARTIFACT_DIR`` (explicit).
      2. ``<AURA_AUDIT_DIR>/../artifacts`` — keeps the audit chain and the
         content store on the same volume in a real deployment, and means
         a test setting ``AURA_AUDIT_DIR=tmp_path`` doesn't have to also
         set the artifact path explicitly.
      3. ``/var/log/aura/artifacts`` (default on the Helm WORM PVC).
    """
    explicit = os.getenv("AURA_ARTIFACT_DIR")
    if explicit:
        p = Path(explicit)
    else:
        audit = os.getenv("AURA_AUDIT_DIR")
        if audit:
            p = Path(audit).parent / "artifacts"
        else:
            p = Path("/var/log/aura/artifacts")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path_for(record_hash: str, ext: str = "json") -> Path:
    if not record_hash or not all(c in "0123456789abcdef" for c in record_hash):
        raise ValueError(f"record_hash must be lowercase hex; got {record_hash!r}")
    return artifact_dir() / f"{record_hash}.{ext}"


def write_artifact(record_hash: str, payload: Dict[str, Any]) -> Path:
    """Persist ``payload`` to ``<record_hash>.json`` as canonical JSON.

    Idempotent: writing the same payload twice produces the same bytes
    and yields the same file. Returns the path written.

    The payload should be the *full* artifact dict (including
    ``audit_record_hash`` and ``rendered`` so replay returns the
    operator/auditor/analyst view the original requester saw).
    """
    path = _path_for(record_hash)
    body = canonical_dumps(payload)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)   # atomic on POSIX & Windows
    logger.debug("Persisted artifact %s (%d bytes)", record_hash[:12], len(body))
    return path


def read_artifact(record_hash: str) -> Optional[Dict[str, Any]]:
    """Return the persisted artifact dict, or ``None`` if not found."""
    path = _path_for(record_hash)
    if not path.exists():
        return None
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Persisted artifact %s unreadable: %s", record_hash[:12], exc)
        return None


def read_artifact_bytes(record_hash: str) -> Optional[bytes]:
    """Return the raw canonical-JSON bytes — used for signature verification."""
    path = _path_for(record_hash)
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError as exc:
        logger.warning("Persisted artifact %s unreadable: %s", record_hash[:12], exc)
        return None


def write_signature(record_hash: str, signature_b64: str) -> Path:
    """Persist a base64 signature alongside the artifact."""
    path = _path_for(record_hash, ext="sig")
    tmp = path.with_suffix(".sig.tmp")
    tmp.write_text(signature_b64, encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_signature(record_hash: str) -> Optional[str]:
    """Return the base64 signature string, or ``None`` if missing."""
    path = _path_for(record_hash, ext="sig")
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def list_record_hashes() -> list[str]:
    """Enumerate every persisted record_hash. Cheap; used by the verifier."""
    d = artifact_dir()
    return sorted(p.stem for p in d.glob("*.json"))
