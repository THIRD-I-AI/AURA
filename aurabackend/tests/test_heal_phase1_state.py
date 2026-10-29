"""Self-healing phase 1: durable UASR drift state.

BUG-567: the drift state store defaulted to in-memory and the free-tier compose
never chose a durable one, so every redeploy wiped every source's baseline and
the first batch after a deploy became the new "normal", whatever it held.

These tests use the real SqliteStateStore on a temp file. A "restart" is a new
store and a new DriftDetector built from the same path, which is all a process
restart leaves behind.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest
import yaml

from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload, DriftType
from uasr.runtime_config import build_state_store, deployment_summary
from uasr.state_store import SourceState

_COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "aws-free-tier" / "docker-compose.yml"


def _batch(
    source_id: str, mu: float = 0.0, n: int = 400, seed: int = 7, col: str = "value", sigma: float = 1.0
) -> BatchPayload:
    rng = random.Random(seed)
    return BatchPayload(
        source_id=source_id,
        batch_id=f"b-{seed}",
        columns=[col, "id"],
        rows=[{col: rng.gauss(mu, sigma), "id": i} for i in range(n)],
    )


@pytest.fixture
def sqlite_env(monkeypatch, tmp_path):
    path = tmp_path / "state" / "uasr_state.db"  # parent dir does not exist yet
    monkeypatch.setenv("UASR_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("UASR_STATE_PATH", str(path))
    return path


def _restart(**kw) -> DriftDetector:
    """A fresh process: nothing survives except what the store wrote to disk."""
    return DriftDetector(state_store=build_state_store(), **kw)


def test_bug567_baseline_survives_restart_and_statistical_drift_is_detected(sqlite_env):
    det = _restart(warmup_batches=0)
    det.register_baseline("orders", _batch("orders", mu=0.0, seed=1))
    assert sqlite_env.exists()

    det2 = _restart(warmup_batches=0)
    assert det2.has_baseline("orders")
    result = det2.detect(_batch("orders", mu=6.0, seed=2))
    assert result.drift_detected is True
    assert result.drift_type == DriftType.STATISTICAL


def test_bug567_schema_baseline_survives_restart_and_a_rename_is_detected(sqlite_env):
    det = _restart()
    det.register_baseline("users", _batch("users", seed=1))
    det.detect(_batch("users", seed=3))  # records the first-seen schema

    det2 = _restart()
    result = det2.detect(_batch("users", seed=4, col="amount"))  # "value" renamed
    assert result.drift_detected is True
    assert result.drift_type == DriftType.SCHEMA


def test_bug567_warmup_progress_survives_restart_so_drift_is_not_suppressed(sqlite_env):
    # Warmup suppresses a statistical alert whose location moved <=2 sigma for
    # a source's first 5 batches, counted from kl_history. Lose that history
    # and every deploy re-opens the window: a spread change (sigma 1 -> 4,
    # mean unchanged) on the first post-deploy batch is then not reported.
    det = _restart()
    det.register_baseline("s", _batch("s", seed=1))
    for seed in range(10, 16):
        det.detect(_batch("s", seed=seed))

    det2 = _restart()
    assert len(det2._store.load("s").kl_history) == 6
    result = det2.detect(_batch("s", sigma=4.0, seed=99))
    assert result.drift_detected is True
    assert result.drift_type == DriftType.STATISTICAL


def test_bug567_every_source_state_field_round_trips_through_the_file(sqlite_env):
    store = build_state_store()
    det = DriftDetector(state_store=store)
    det.register_baseline("s", _batch("s", seed=1))
    st = store.load("s")
    st.schema = {"value": "float", "id": "int"}
    st.kl_history = [0.01, 0.02]
    st.embeddings = [[0.5, 0.25]]
    st.schema_intent = {"added": {"x": "int"}, "removed": [], "type_changes": {},
                        "expires_at": 1.5, "note": None, "actor": "ci"}
    store.save("s", st)

    back = build_state_store().load("s")
    assert back.schema == st.schema
    assert back.kl_history == st.kl_history
    assert back.embeddings == st.embeddings
    assert back.schema_intent == st.schema_intent
    assert {k: v.model_dump() for k, v in back.baseline.items()} == {
        k: v.model_dump() for k, v in st.baseline.items()
    }


def test_bug567_store_contract_contains_delete_and_listing(sqlite_env):
    store = build_state_store()
    assert store.load("absent").is_empty()
    assert not store.contains("absent")  # an absent load must not insert a record
    store.save("a", SourceState(kl_history=[1.0]))
    store.save("b", SourceState(schema={"c": "int"}))
    store.save("a", SourceState(kl_history=[2.0]))  # overwrite, not a second row
    reopened = build_state_store()
    assert sorted(reopened.source_ids()) == ["a", "b"]
    assert reopened.load("a").kl_history == [2.0]
    reopened.delete("a")
    assert sorted(build_state_store().source_ids()) == ["b"]


def test_bug567_deployment_summary_reports_the_durable_backend(sqlite_env):
    s = deployment_summary()
    assert s["state_backend"] == "sqlite"
    assert s["state_path"] == str(sqlite_env)


def test_bug567_free_tier_compose_defaults_uasr_state_to_sqlite_on_the_data_volume():
    services = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]
    svc = services["uasr_service"]
    env = svc["environment"]
    # ${UASR_STATE_BACKEND:-sqlite}: the default when .env does not override it.
    assert env["UASR_STATE_BACKEND"].endswith(":-sqlite}")
    path = env["UASR_STATE_PATH"]
    assert path.startswith("/data/")
    assert any(v.split(":")[1] == "/data" for v in svc["volumes"])
    # Not the SQLAlchemy file (two writers on one SQLite file) nor the DuckDB lake.
    assert path not in (env["METADATA_DATABASE_URL"].split("////", 1)[-1], env["UASR_DUCKDB_PATH"])
