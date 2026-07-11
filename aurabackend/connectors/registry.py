"""
Connector Registry
==================
Single source of truth for connectors AURA knows how to talk to.

Replaces the hardcoded ``/connectors/available`` lists in
``connections.py`` and ``connectors/main.py``. New connectors register
themselves at import time via ``register_connector(spec)`` — both the
gateway and the connectors microservice read from the same registry.

A spec describes:
  * ``id`` — stable string used as the SQL-builder discriminator
  * ``kind`` — one of ``relational`` | ``warehouse`` | ``embedded``
  * ``capabilities`` — feature flags (``sql``, ``vector``, ``spatial`` …)
  * ``fields`` — UI-driven config schema for the Connections form

The schema is intentionally JSON-friendly (no ABCs / Pydantic) so it can
be returned over the wire and rendered by a generic form on the
frontend.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

_log = logging.getLogger("aura.connectors.registry")
ENTRY_POINT_GROUP = "aura.connectors"


FieldType = Literal["string", "secret", "number", "boolean", "textarea"]
ConnectorKind = Literal["relational", "warehouse", "embedded", "stream"]


@dataclass(frozen=True)
class ConnectorField:
    """One config knob the user must fill in to connect."""
    key: str
    label: str
    type: FieldType = "string"
    required: bool = False
    default: Any = None
    placeholder: Optional[str] = None
    help: Optional[str] = None


@dataclass(frozen=True)
class ConnectorSpec:
    """Public-facing description of a connector type."""
    id: str
    name: str
    description: str
    kind: ConnectorKind
    icon: str = ""
    capabilities: List[str] = field(default_factory=list)
    fields: List[ConnectorField] = field(default_factory=list)
    available: bool = True
    unavailable_reason: Optional[str] = None
    docs_url: Optional[str] = None
    # Not part of the wire contract: a callable ``(config) -> connector``
    # instance, consumed by ``build_connector``. Excluded from ``to_dict``
    # so the spec stays JSON-serialisable for the frontend form.
    factory: Optional[Callable[[Any], Any]] = field(default=None, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("factory", None)
        # Backwards-compat shim — older clients read ``config_required``.
        d["config_required"] = [f["key"] for f in d["fields"] if f.get("required")]
        return d


# ── Registry storage ────────────────────────────────────────────────

_lock = threading.Lock()
_registry: Dict[str, ConnectorSpec] = {}


def register_connector(spec: ConnectorSpec) -> None:
    """Idempotent registration. Re-registering the same id replaces the spec."""
    with _lock:
        _registry[spec.id] = spec


def unregister_connector(connector_id: str) -> None:
    with _lock:
        _registry.pop(connector_id, None)


def get_connector(connector_id: str) -> Optional[ConnectorSpec]:
    with _lock:
        return _registry.get(connector_id)


def available_connectors(*, include_unavailable: bool = True) -> List[ConnectorSpec]:
    """All registered specs, sorted by name. Filter with ``include_unavailable=False``
    to hide entries whose driver isn't installed."""
    with _lock:
        specs = list(_registry.values())
    if not include_unavailable:
        specs = [s for s in specs if s.available]
    specs.sort(key=lambda s: s.name.lower())
    return specs


def build_connector(connector_id: str, config: Any) -> Optional[Any]:
    """Instantiate a live connector for a registered id.

    DB-agnostic entry point used by the gateway instead of a hardcoded
    ``type -> class`` switch. Returns ``None`` when the id is unknown or
    its spec carries no factory (e.g. the driver isn't installed), so
    callers keep their existing ``if connector is None`` handling.
    """
    spec = get_connector(connector_id)
    if spec is None or spec.factory is None:
        return None
    return spec.factory(config)


# ── Built-in connector specs ────────────────────────────────────────

_DB_FIELDS = [
    ConnectorField("host", "Host", "string", required=True, placeholder="db.example.com"),
    ConnectorField("port", "Port", "number", required=True),
    ConnectorField("database", "Database", "string", required=True),
    ConnectorField("username", "Username", "string", required=True),
    ConnectorField("password", "Password", "secret", required=True),
    ConnectorField("ssl", "Use SSL", "boolean", default=False),
]


def _seed_builtins() -> None:
    """Register the connectors AURA ships with. Each spec checks for its
    driver — if the import failed in ``connectors.__init__``, we still
    register the spec but mark it ``available=False`` so the UI can
    show *why* the option is greyed out (missing dep)."""
    try:
        from connectors import (
            BigQueryConnector,
            DuckDBConnector,
            FAISSConnector,
            MySQLConnector,
            PostgreSQLConnector,
        )
    except ImportError:  # pragma: no cover — defensive
        BigQueryConnector = DuckDBConnector = MySQLConnector = PostgreSQLConnector = FAISSConnector = None  # type: ignore

    register_connector(ConnectorSpec(
        id="postgresql",
        name="PostgreSQL",
        description="PostgreSQL database (+ pgvector, PostGIS extensions).",
        kind="relational",
        icon="🐘",
        capabilities=["sql", "vector", "spatial"],
        fields=list(_DB_FIELDS) + [ConnectorField("port", "Port", "number", required=True, default=5432)],
        factory=(lambda cfg: PostgreSQLConnector(cfg)) if PostgreSQLConnector else None,
        available=PostgreSQLConnector is not None,
        unavailable_reason=None if PostgreSQLConnector else "asyncpg driver not installed",
    ))
    register_connector(ConnectorSpec(
        id="mysql",
        name="MySQL",
        description="MySQL or MariaDB database.",
        kind="relational",
        icon="🐬",
        capabilities=["sql"],
        fields=list(_DB_FIELDS) + [ConnectorField("port", "Port", "number", required=True, default=3306)],
        factory=(lambda cfg: MySQLConnector(cfg)) if MySQLConnector else None,
        available=MySQLConnector is not None,
        unavailable_reason=None if MySQLConnector else "aiomysql driver not installed",
    ))
    register_connector(ConnectorSpec(
        id="bigquery",
        name="Google BigQuery",
        description="BigQuery data warehouse.",
        kind="warehouse",
        icon="☁️",
        capabilities=["sql"],
        fields=[
            ConnectorField("project_id", "GCP Project ID", "string", required=True),
            ConnectorField("dataset", "Default Dataset", "string"),
            ConnectorField("credentials_json", "Service Account JSON", "textarea", required=True,
                           help="Paste the service account key JSON. Stored encrypted."),
        ],
        factory=(lambda cfg: BigQueryConnector(cfg)) if BigQueryConnector else None,
        available=BigQueryConnector is not None,
        unavailable_reason=None if BigQueryConnector else "google-cloud-bigquery not installed",
    ))
    register_connector(ConnectorSpec(
        id="duckdb",
        name="DuckDB",
        description="Fast local analytics — query CSV / Parquet / JSON files directly.",
        kind="embedded",
        icon="🦆",
        capabilities=["sql", "file_query"],
        fields=[
            ConnectorField("database", "DB file (or :memory:)", "string", default=":memory:"),
        ],
        factory=(lambda cfg: DuckDBConnector(cfg)) if DuckDBConnector else None,
        available=DuckDBConnector is not None,
    ))
    # Sprint 17 — Multi-Modal Fabric (Pillar 2). DuckDB-spatial is the
    # same connector class with the spatial extension auto-loaded; we
    # expose it as a separate spec entry so users can pick it from the
    # UI without remembering to set extra_params.
    register_connector(ConnectorSpec(
        id="duckdb_spatial",
        name="DuckDB (spatial)",
        description=(
            "DuckDB with the spatial extension preloaded. "
            "Supports ST_* PostGIS-style functions and R-tree spatial joins "
            "without needing a Postgres server."
        ),
        kind="embedded",
        icon="🗺️",
        capabilities=["sql", "file_query", "spatial"],
        fields=[
            ConnectorField("database", "DB file (or :memory:)", "string", default=":memory:"),
        ],
        factory=(lambda cfg: DuckDBConnector(cfg)) if DuckDBConnector else None,
        available=DuckDBConnector is not None,
        unavailable_reason=(
            None if DuckDBConnector
            else "duckdb driver not installed"
        ),
    ))
    register_connector(ConnectorSpec(
        id="faiss",
        name="FAISS (vector)",
        description=(
            "In-process FAISS vector index — zero external server required. "
            "Cosine / L2 / HNSW search over arbitrary embeddings with a "
            "sidecar metadata frame for relational joins."
        ),
        kind="embedded",
        icon="🧭",
        capabilities=["vector"],
        fields=[
            ConnectorField(
                "database", "Persistence path (blank = in-memory)", "string",
                help=(
                    "If set, the index + metadata are persisted to this path "
                    "on every add_vectors call. Blank = ephemeral."
                ),
            ),
            ConnectorField(
                "dimension", "Embedding dimension", "number", default=384,
                help="Must match the model that produced the vectors.",
            ),
            ConnectorField(
                "index_type", "Index type", "string", default="flat_ip",
                help="flat_ip (cosine, exact), l2 (Euclidean), hnsw (approx).",
            ),
        ],
        factory=(lambda cfg: FAISSConnector(cfg)) if FAISSConnector else None,
        available=FAISSConnector is not None,
        unavailable_reason=(
            None if FAISSConnector
            else "faiss-cpu not installed (pip install -r requirements-multimodal.txt)"
        ),
    ))


def _load_entry_point_connectors() -> None:
    """Discover third-party connectors via the ``aura.connectors`` entry-point
    group (``[project.entry-points."aura.connectors"]`` in their pyproject).

    Each entry point is loaded and called with no arguments. It must return
    either a single ``ConnectorSpec`` or an iterable of them. Errors from any
    one entry point are logged and swallowed so a broken plugin can't break
    the whole registry."""
    try:
        from importlib import metadata as importlib_metadata
    except ImportError:  # pragma: no cover — Python <3.8
        return

    try:
        eps = importlib_metadata.entry_points()
    except Exception as exc:  # pragma: no cover — defensive
        _log.warning("entry_points() failed: %s", exc)
        return

    # Python 3.10+ supports .select(); older returns a dict.
    if hasattr(eps, "select"):
        group = eps.select(group=ENTRY_POINT_GROUP)
    else:
        group = eps.get(ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]

    for ep in group:
        try:
            factory = ep.load()
            result = factory() if callable(factory) else factory
        except Exception as exc:
            _log.warning("connector entry point %s failed to load: %s", ep.name, exc)
            continue

        specs = result if isinstance(result, (list, tuple)) else [result]
        for spec in specs:
            if isinstance(spec, ConnectorSpec):
                register_connector(spec)
            else:
                _log.warning(
                    "entry point %s returned non-ConnectorSpec value: %r",
                    ep.name, spec,
                )


_seed_builtins()
_load_entry_point_connectors()
