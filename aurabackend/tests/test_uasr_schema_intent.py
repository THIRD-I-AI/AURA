"""
Schema-evolution intent — Tier A (pure Python, no optional deps).

Covers:
  * declare_schema_intent no-ops when the feature is off
  * an intentional, declared schema change (added/removed/type-change) is
    NOT flagged as drift and the batch's schema becomes the new baseline
  * an intent is consumed (cleared) once the declared target is fully
    reached, but stays active across partial batches until then
  * an intent that expired (TTL) does not suppress drift
  * a genuine, UNDECLARED schema-drift batch is still flagged — regression
    coverage that an intent only ever suppresses the specific change it
    named, never anything else
  * SourceState.schema_intent round-trips through to_json/from_json
  * POST /uasr/schema-intent: feature-flag-off 400, bad input 422, and the
    happy path recording an intent the detector then honors
"""
from __future__ import annotations

import importlib
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload, DriftType
from uasr.state_store import InMemoryStateStore, SourceState


def _baseline_batch(source_id="src1"):
    # Both columns fully numeric -- _compute_batch_embedding returns None for
    # an all-numeric batch (see drift_detector.py), so no reference embedding
    # is registered and the semantic-drift channel stays inert. That keeps
    # these schema-intent tests isolated to the schema-drift channel instead
    # of also tripping semantic drift on an unrelated small-sample embedding
    # distance -- schema drift is checked and returned before the
    # statistical/semantic channels run, but register_baseline (used to set
    # up these fixtures) registers an embedding independently of that order.
    return BatchPayload(
        source_id=source_id,
        batch_id="baseline",
        columns=["a", "b"],
        rows=[{"a": 1, "b": 10}, {"a": 2, "b": 20}],
        schema_snapshot={"a": "int", "b": "int"},
    )


class TestDeclareSchemaIntentFlagOff:
    def test_noop_when_disabled(self):
        det = DriftDetector(schema_intent_enabled=False)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent("src1", added={"c": "int"})

        st = det._store.peek("src1")
        assert st.schema_intent is None

    def test_undeclared_intent_falls_through_even_if_state_manually_set(self):
        # Belt-and-braces: even if a schema_intent dict were present on the
        # state (e.g. leftover from a flag flip), the detector must not
        # consult it while the flag is off.
        det = DriftDetector(schema_intent_enabled=False)
        det.register_baseline("src1", _baseline_batch())
        st = det._store.load("src1")
        st.schema_intent = {
            "added": {"c": "int"},
            "removed": [],
            "type_changes": {},
            "expires_at": time.time() + 3600,
            "note": None,
            "actor": None,
        }
        det._store.save("src1", st)

        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b", "c"],
            rows=[{"a": 1, "b": 10, "c": 5}],
            schema_snapshot={"a": "int", "b": "int", "c": "int"},
        )
        result = det.detect(batch)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.SCHEMA


class TestDeclaredIntentSuppressesDrift:
    def test_declared_added_column_not_flagged(self):
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent("src1", added={"c": "int"}, ttl_seconds=3600)

        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b", "c"],
            rows=[{"a": 1, "b": 10, "c": 5}],
            schema_snapshot={"a": "int", "b": "int", "c": "int"},
        )
        result = det.detect(batch)
        assert result.drift_detected is False

        # New baseline adopted
        st = det._store.peek("src1")
        assert st.schema == {"a": "int", "b": "int", "c": "int"}
        # Target fully reached (declared "c" now present) -> intent cleared
        assert st.schema_intent is None

    def test_declared_removed_column_not_flagged(self):
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent("src1", removed=["b"], ttl_seconds=3600)

        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a"],
            rows=[{"a": 1}],
            schema_snapshot={"a": "int"},
        )
        result = det.detect(batch)
        assert result.drift_detected is False
        st = det._store.peek("src1")
        assert st.schema == {"a": "int"}
        assert st.schema_intent is None

    def test_declared_type_change_not_flagged(self):
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent("src1", type_changes={"a": "float"}, ttl_seconds=3600)

        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b"],
            rows=[{"a": 1.5, "b": 10}],
            schema_snapshot={"a": "float", "b": "int"},
        )
        result = det.detect(batch)
        assert result.drift_detected is False
        st = det._store.peek("src1")
        assert st.schema == {"a": "float", "b": "int"}

    def test_intent_stays_active_until_target_fully_reached(self):
        # Two declared additions, only one appears this batch (gradual
        # rollout across app instances) -- intent must survive so the next
        # batch's arrival of the other column is also covered.
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent(
            "src1", added={"c": "int", "d": "int"}, ttl_seconds=3600
        )

        batch1 = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b", "c"],
            rows=[{"a": 1, "b": 10, "c": 5}],
            schema_snapshot={"a": "int", "b": "int", "c": "int"},
        )
        result1 = det.detect(batch1)
        assert result1.drift_detected is False
        st = det._store.peek("src1")
        assert st.schema_intent is not None  # "d" not yet reached

        batch2 = BatchPayload(
            source_id="src1",
            batch_id="b3",
            columns=["a", "b", "c", "d"],
            rows=[{"a": 1, "b": 10, "c": 5, "d": 9}],
            schema_snapshot={"a": "int", "b": "int", "c": "int", "d": "int"},
        )
        result2 = det.detect(batch2)
        assert result2.drift_detected is False
        st2 = det._store.peek("src1")
        assert st2.schema == {"a": "int", "b": "int", "c": "int", "d": "int"}
        assert st2.schema_intent is None

    def test_expired_intent_does_not_suppress_drift(self):
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent("src1", added={"c": "int"}, ttl_seconds=-1)

        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b", "c"],
            rows=[{"a": 1, "b": 10, "c": 5}],
            schema_snapshot={"a": "int", "b": "int", "c": "int"},
        )
        result = det.detect(batch)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.SCHEMA


class TestUndeclaredDriftStillFlagged:
    """Regression: an intent must never widen coverage beyond what it named."""

    def test_undeclared_added_column_still_flagged(self):
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        # No intent declared at all.
        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b", "c"],
            rows=[{"a": 1, "b": 10, "c": 5}],
            schema_snapshot={"a": "int", "b": "int", "c": "int"},
        )
        result = det.detect(batch)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.SCHEMA
        assert "c" in result.affected_columns

    def test_extra_undeclared_column_on_top_of_declared_one_still_flagged(self):
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent("src1", added={"c": "int"}, ttl_seconds=3600)

        # Batch adds BOTH the declared "c" and an undeclared "z" -- must
        # still be flagged, not silently swallowed.
        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b", "c", "z"],
            rows=[{"a": 1, "b": 10, "c": 5, "z": 1}],
            schema_snapshot={"a": "int", "b": "int", "c": "int", "z": "int"},
        )
        result = det.detect(batch)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.SCHEMA
        assert set(result.affected_columns) == {"c", "z"}

    def test_undeclared_type_change_still_flagged(self):
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent("src1", added={"c": "int"}, ttl_seconds=3600)

        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b"],
            rows=[{"a": 1.5, "b": 10}],
            schema_snapshot={"a": "float", "b": "int"},
        )
        result = det.detect(batch)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.SCHEMA
        assert "a" in result.affected_columns

    def test_declared_type_change_does_not_cover_a_different_observed_type(self):
        # Intent sanctions "a: int -> float". A batch where "a" instead
        # shows up as e.g. "string" must NOT be silently absorbed just
        # because the column name matches -- that would mask a genuine,
        # unrelated data-corruption event for the whole TTL window.
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent("src1", type_changes={"a": "float"}, ttl_seconds=3600)

        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b"],
            rows=[{"a": "corrupt", "b": 10}],
            schema_snapshot={"a": "string", "b": "int"},
        )
        result = det.detect(batch)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.SCHEMA
        assert "a" in result.affected_columns
        # Intent must still be live -- the covered change never happened,
        # so it hasn't been consumed.
        st = det._store.peek("src1")
        assert st.schema_intent is not None

    def test_declared_added_column_does_not_cover_a_different_observed_type(self):
        # Same coverage bug on the added-column path: intent sanctions
        # "c: int" but the batch's "c" shows up typed "string".
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline("src1", _baseline_batch())
        det.declare_schema_intent("src1", added={"c": "int"}, ttl_seconds=3600)

        batch = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b", "c"],
            rows=[{"a": 1, "b": 10, "c": "oops"}],
            schema_snapshot={"a": "int", "b": "int", "c": "string"},
        )
        result = det.detect(batch)
        assert result.drift_detected is True
        assert result.drift_type == DriftType.SCHEMA
        assert "c" in result.affected_columns


class TestMultiColumnTypeChangeIntentGradualRollout:
    """Regression for the target_reached bug: clearing the intent after only
    the first declared type-change column landed spuriously flagged the
    still-pending declared column as drift once the intent was gone.
    """

    def test_intent_stays_active_until_all_declared_type_changes_land(self):
        det = DriftDetector(schema_intent_enabled=True)
        det.register_baseline(
            "src1",
            BatchPayload(
                source_id="src1",
                batch_id="baseline",
                columns=["a", "b"],
                rows=[{"a": 1, "b": 10}, {"a": 2, "b": 20}],
                schema_snapshot={"a": "int", "b": "int"},
            ),
        )
        det.declare_schema_intent(
            "src1", type_changes={"a": "float", "b": "float"}, ttl_seconds=3600
        )

        # Batch 1: only "a" has rolled over to float yet (gradual rollout).
        batch1 = BatchPayload(
            source_id="src1",
            batch_id="b2",
            columns=["a", "b"],
            rows=[{"a": 1.5, "b": 10}],
            schema_snapshot={"a": "float", "b": "int"},
        )
        result1 = det.detect(batch1)
        assert result1.drift_detected is False
        st = det._store.peek("src1")
        # Must still be active -- "b" hasn't reached its declared target yet.
        assert st.schema_intent is not None

        # Batch 2: "b" still hasn't changed. Since the intent is still
        # live and still declares "b: float", this must keep being
        # tolerated, not flagged as drift.
        batch2 = BatchPayload(
            source_id="src1",
            batch_id="b3",
            columns=["a", "b"],
            rows=[{"a": 1.6, "b": 11}],
            schema_snapshot={"a": "float", "b": "int"},
        )
        result2 = det.detect(batch2)
        assert result2.drift_detected is False

        # Batch 3: "b" finally rolls over to float -- target now fully
        # reached, intent is consumed.
        batch3 = BatchPayload(
            source_id="src1",
            batch_id="b4",
            columns=["a", "b"],
            rows=[{"a": 1.7, "b": 12.0}],
            schema_snapshot={"a": "float", "b": "float"},
        )
        result3 = det.detect(batch3)
        assert result3.drift_detected is False
        st3 = det._store.peek("src1")
        assert st3.schema == {"a": "float", "b": "float"}
        assert st3.schema_intent is None


class TestSourceStateRoundtrip:
    def test_schema_intent_roundtrips_via_json(self):
        st = SourceState(
            schema={"a": "int"},
            schema_intent={
                "added": {"c": "int"},
                "removed": ["b"],
                "type_changes": {"a": "float"},
                "expires_at": 1234567890.5,
                "note": "migration-42",
                "actor": "ci-bot",
            },
        )
        back = SourceState.from_json(st.to_json())
        assert back.schema_intent == st.schema_intent

    def test_schema_intent_survives_store_save_load(self):
        store = InMemoryStateStore()
        st = store.load("src1")
        st.schema_intent = {
            "added": {},
            "removed": ["x"],
            "type_changes": {},
            "expires_at": 999.0,
            "note": None,
            "actor": None,
        }
        store.save("src1", st)
        reloaded = store.load("src1")
        assert reloaded.schema_intent == st.schema_intent

    def test_is_empty_false_when_only_schema_intent_set(self):
        st = SourceState(schema_intent={"added": {}, "removed": [], "type_changes": {}, "expires_at": 1.0})
        assert st.is_empty() is False


# ── POST /uasr/schema-intent endpoint ─────────────────────────────


class TestSchemaIntentEndpoint:
    """Calls the FastAPI handler function directly (no ASGI lifespan) --
    matches this repo's established pattern (see
    test_uasr_service_cross_source_heal.py) for avoiding the aiosqlite
    non-daemon-thread pytest hang on Windows (BUG-008).

    UASR_SCHEMA_INTENT_ENABLED is resolved once at ``uasr.service`` import
    time (same pattern as UASR_RISK_TIERED / UASR_USE_CAUSAL_RL_EVALUATOR
    in that module, see test_uasr_runtime_config.py) -- reload the module
    under the flag to prove the wiring, then reload again in ``finally``
    so later tests see the module's default (flag-off) state.
    """

    def _reload_with_flag(self, monkeypatch, enabled: bool):
        monkeypatch.setenv("UASR_SCHEMA_INTENT_ENABLED", "true" if enabled else "false")
        service_module = importlib.import_module("uasr.service")
        importlib.reload(service_module)
        return service_module

    def _restore(self, monkeypatch):
        monkeypatch.delenv("UASR_SCHEMA_INTENT_ENABLED", raising=False)
        service_module = importlib.import_module("uasr.service")
        importlib.reload(service_module)
        assert service_module._SCHEMA_INTENT_ENABLED is False

    def test_disabled_returns_400(self, monkeypatch):
        import asyncio

        service_module = self._reload_with_flag(monkeypatch, enabled=False)
        try:
            req = service_module.SchemaIntentRequest(source_id="src1", added_columns={"c": "int"})
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(service_module.declare_schema_intent(req))
            assert exc_info.value.status_code == 400
        finally:
            self._restore(monkeypatch)

    def test_enabled_declares_and_detector_honors_it(self, monkeypatch):
        import asyncio

        service_module = self._reload_with_flag(monkeypatch, enabled=True)
        try:
            service_module._detector.register_baseline("src_ep", _baseline_batch("src_ep"))

            req = service_module.SchemaIntentRequest(
                source_id="src_ep",
                added_columns={"c": "int"},
                note="migration-1",
                actor="ci",
            )
            result = asyncio.run(service_module.declare_schema_intent(req))
            assert result["status"] == "declared"
            assert result["source_id"] == "src_ep"
            assert result["expires_in_seconds"] == service_module._SCHEMA_INTENT_DEFAULT_TTL_SECONDS

            batch = BatchPayload(
                source_id="src_ep",
                batch_id="b2",
                columns=["a", "b", "c"],
                rows=[{"a": 1, "b": 10, "c": 5}],
                schema_snapshot={"a": "int", "b": "int", "c": "int"},
            )
            detect_result = service_module._detector.detect(batch)
            assert detect_result.drift_detected is False
        finally:
            self._restore(monkeypatch)

    def test_missing_source_id_is_rejected_by_pydantic(self, monkeypatch):
        service_module = self._reload_with_flag(monkeypatch, enabled=True)
        try:
            from pydantic import ValidationError

            with pytest.raises(ValidationError):
                service_module.SchemaIntentRequest()
        finally:
            self._restore(monkeypatch)

    def test_custom_ttl_seconds_is_honored(self, monkeypatch):
        import asyncio

        service_module = self._reload_with_flag(monkeypatch, enabled=True)
        try:
            service_module._detector.register_baseline("src_ttl", _baseline_batch("src_ttl"))
            req = service_module.SchemaIntentRequest(
                source_id="src_ttl", added_columns={"c": "int"}, ttl_seconds=120
            )
            result = asyncio.run(service_module.declare_schema_intent(req))
            assert result["expires_in_seconds"] == 120
        finally:
            self._restore(monkeypatch)
