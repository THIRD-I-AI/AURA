"""
Critic-output cache for replay determinism.

Why this exists: the adversarial critic is an LLM call. LLMs are not
strictly deterministic even at temperature=0. If we re-ran the engine
during replay, the critic's challenge text would drift, the artifact
would re-hash differently, and replay would always fail.

Resolution: cache the critic's output keyed by a stable triple of
``(request_hash, model_id, model_version)``. Replay reads from the
cache; cache miss → critic re-runs and the new artifact gets
``regenerated_critic: true``, which the auditor view surfaces as a
diff signal.

Storage layout (parallel to the artifact store under
``AURA_CRITIC_CACHE_DIR``, default ``/var/log/aura/critic-cache``):

  critic-cache/
      <key>.json   # the cached critic output verbatim, canonical JSON

Cache entries are append-only — we never overwrite a hit. A cache miss
on a key that has ever been seen would only happen if the key was
deleted manually, which is itself a TRAIGA event; the engine writes
the regenerated body to a NEW key (deterministic, since the input is
the same) anyway.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .canonical import canonical_dumps

logger = logging.getLogger("aura.counterfactual.critic_cache")


def cache_dir() -> Path:
    """Return the configured critic-cache directory, creating it if needed.

    Same resolution policy as the artifact store — explicit env wins,
    otherwise sit next to the audit log so tests pinning ``AURA_AUDIT_DIR``
    don't have to know about this directory.
    """
    explicit = os.getenv("AURA_CRITIC_CACHE_DIR")
    if explicit:
        p = Path(explicit)
    else:
        audit = os.getenv("AURA_AUDIT_DIR")
        if audit:
            p = Path(audit).parent / "critic-cache"
        else:
            p = Path("/var/log/aura/critic-cache")
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_key(*, request_hash: str, model_id: str, model_version: str) -> str:
    """sha256 over canonical(request_hash, model_id, model_version)."""
    h = hashlib.sha256()
    h.update(canonical_dumps({
        "request_hash": request_hash,
        "model_id": model_id,
        "model_version": model_version,
    }).encode("utf-8"))
    return h.hexdigest()


def _path_for(key: str) -> Path:
    if not key or not all(c in "0123456789abcdef" for c in key):
        raise ValueError(f"cache key must be lowercase hex; got {key!r}")
    return cache_dir() / f"{key}.json"


def get(key: str) -> Optional[List[Dict[str, Any]]]:
    """Return the cached challenges list, or ``None`` on cache miss."""
    path = _path_for(key)
    if not path.exists():
        return None
    import json
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(body, dict) and "challenges" in body:
            return body["challenges"]
        if isinstance(body, list):
            return body
        logger.warning("Critic cache %s has unexpected shape; treating as miss", key[:12])
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Critic cache %s unreadable (%s); treating as miss", key[:12], exc)
        return None


def put(key: str, challenges: List[Dict[str, Any]]) -> None:
    """Persist ``challenges`` under ``key`` as canonical JSON.

    Idempotent: writing the same payload twice produces the same bytes.
    Atomic via write-to-tmp + os.replace.
    """
    path = _path_for(key)
    body = canonical_dumps({"challenges": challenges})
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
    logger.debug("Cached critic output %s (%d challenges)", key[:12], len(challenges))
