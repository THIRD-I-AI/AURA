"""
Inbound Webhook Registry
=========================
Stores user-defined HTTP triggers that, when POSTed to ``/hooks/{slug}``,
launch either a saved pipeline or an agent prompt.

Each hook record:
  - id, slug (URL path), kind ("pipeline" | "agent"), target (pipeline_id or
    prompt template), secret (optional HMAC-SHA256 verification on
    X-AURA-Signature), active flag, headers/templating not yet supported.

Persistence: ``data/webhooks/inbound.json`` (same dir as outbound subs).

The actual trigger logic lives in the router so this module stays free of
FastAPI / engine imports.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aura.inbound_hooks")

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "webhooks",
)
_STORE_PATH = os.path.join(_DATA_DIR, "inbound.json")


@dataclass
class InboundHook:
    id: str
    slug: str
    kind: str                          # "pipeline" | "agent"
    target: str                        # pipeline_id OR prompt template
    secret: Optional[str] = None
    active: bool = True
    description: str = ""
    pass_payload_as: Optional[str] = None  # for agent hooks: key under which
                                           # the request body is exposed in
                                           # the agent context (default: ignored)
    last_fired_at: Optional[str] = None
    fire_count: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        if d.get("secret"):
            d["secret"] = "***redacted***"
            d["has_secret"] = True
        else:
            d["has_secret"] = False
        return d


class InboundHookRegistry:
    def __init__(self) -> None:
        self._hooks: Dict[str, InboundHook] = {}      # by id
        self._by_slug: Dict[str, InboundHook] = {}    # by slug
        self._load()

    # ── Persistence ────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(_STORE_PATH):
            return
        try:
            with open(_STORE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for r in raw:
                h = InboundHook(**r)
                self._hooks[h.id] = h
                self._by_slug[h.slug] = h
            logger.info("Loaded %d inbound hooks", len(self._hooks))
        except Exception as exc:
            logger.warning("Failed to load inbound hook store: %s", exc)

    def _save(self) -> None:
        os.makedirs(_DATA_DIR, exist_ok=True)
        try:
            with open(_STORE_PATH, "w", encoding="utf-8") as f:
                json.dump([h.__dict__ for h in self._hooks.values()], f, indent=2)
        except Exception as exc:
            logger.warning("Failed to persist inbound hook store: %s", exc)

    # ── CRUD ───────────────────────────────────────────────────────

    def list(self) -> List[InboundHook]:
        return list(self._hooks.values())

    def get(self, hook_id: str) -> Optional[InboundHook]:
        return self._hooks.get(hook_id)

    def by_slug(self, slug: str) -> Optional[InboundHook]:
        return self._by_slug.get(slug)

    def register(
        self,
        slug: str,
        kind: str,
        target: str,
        secret: Optional[str] = None,
        description: str = "",
        pass_payload_as: Optional[str] = None,
    ) -> InboundHook:
        if kind not in ("pipeline", "agent"):
            raise ValueError("kind must be 'pipeline' or 'agent'")
        if slug in self._by_slug:
            raise ValueError(f"slug '{slug}' already registered")
        hook = InboundHook(
            id=uuid.uuid4().hex,
            slug=slug,
            kind=kind,
            target=target,
            secret=secret,
            description=description,
            pass_payload_as=pass_payload_as,
        )
        self._hooks[hook.id] = hook
        self._by_slug[hook.slug] = hook
        self._save()
        return hook

    def update(self, hook_id: str, **fields) -> Optional[InboundHook]:
        h = self._hooks.get(hook_id)
        if not h:
            return None
        new_slug = fields.get("slug")
        if new_slug and new_slug != h.slug:
            if new_slug in self._by_slug:
                raise ValueError(f"slug '{new_slug}' already registered")
            del self._by_slug[h.slug]
            h.slug = new_slug
            self._by_slug[new_slug] = h
        for k, v in fields.items():
            if k == "slug":
                continue
            if hasattr(h, k) and v is not None:
                setattr(h, k, v)
        self._save()
        return h

    def delete(self, hook_id: str) -> bool:
        h = self._hooks.pop(hook_id, None)
        if h is None:
            return False
        self._by_slug.pop(h.slug, None)
        self._save()
        return True

    def record_fire(self, hook: InboundHook) -> None:
        hook.fire_count += 1
        hook.last_fired_at = datetime.now(timezone.utc).isoformat()
        self._save()


# ── Singleton ─────────────────────────────────────────────────────

inbound_hooks = InboundHookRegistry()
