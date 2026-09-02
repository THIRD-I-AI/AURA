"""
Cross-source auto-heal fan-out (candidate #5) wired into the Kafka MAPE-K
worker -- ``MAPEKWorker._persist_and_maybe_cross_heal``, the helper both
``recovery_failed`` branches in ``mapek_worker.py`` call before giving up.

Deliberately mocks ``uasr.mapek_worker.persist_recovery_row`` and
``uasr.mapek_worker.attempt_cross_source_heal`` at the module level rather
than exercising a real DB session: per docs/BUG_REGISTRY.md's BUG-008,
importing ``uasr.mapek_worker`` (needed here to construct ``MAPEKWorker``)
alongside a real, freshly-created async DB engine in the same short-lived
process has reproducibly hung the interpreter at exit. Mocking out the only
two call sites that would ever touch the DB means this file never creates
one, so the hazard never applies -- the real DB-write/session-threading
behavior is covered without ``mapek_worker`` in
tests/test_uasr_recovery_persistence.py and tests/test_uasr_cross_source_heal.py.

Run in isolation and checked with `ps`/WMI at least a minute after
completion while writing this file -- no leftover process (see the
recovery_persistence/cross_source_heal test files' fixtures for the
separate, real hang this investigation found and fixed: an un-disposed
aiosqlite engine, unrelated to BUG-008's mapek_worker trigger since this
file never opens a DB session at all).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.mapek_worker import MAPEKConfig, MAPEKWorker, _make_canary_transform  # noqa: E402
from uasr.models import (  # noqa: E402
    BatchPayload,
    DriftDetectionResult,
    DriftType,
    RecoveryLoopResult,
    RecoveryStatus,
    ShimResult,
)


def _drift() -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="s_failing", batch_id="batch_001",
        drift_detected=True, drift_type=DriftType.SCHEMA, severity="high",
    )


def _batch() -> BatchPayload:
    return BatchPayload(source_id="s_failing", batch_id="batch_001", rows=[{"a": 1}])


def _failed_recovery() -> RecoveryLoopResult:
    return RecoveryLoopResult(
        drift_event_id="batch_001", recovery_id="rec_001", status=RecoveryStatus.FAILED,
    )


def _worker(correlation_auto_heal: bool, correlation_window_seconds: float = 30.0) -> MAPEKWorker:
    cfg = MAPEKConfig(
        correlation_auto_heal=correlation_auto_heal,
        correlation_window_seconds=correlation_window_seconds,
    )
    return MAPEKWorker(config=cfg, detector=MagicMock(), recovery_loop=MagicMock(), metrics=MagicMock())


class TestConfigDefaults:

    def test_correlation_flags_default_off(self):
        cfg = MAPEKConfig()
        assert cfg.correlation_auto_heal is False
        assert cfg.correlation_window_seconds == 0.0

    def test_correlation_flags_settable(self):
        cfg = MAPEKConfig(correlation_auto_heal=True, correlation_window_seconds=45.0)
        assert cfg.correlation_auto_heal is True
        assert cfg.correlation_window_seconds == 45.0


class TestPersistAndMaybeCrossHeal:

    @pytest.mark.asyncio
    async def test_feature_off_persists_without_return_row_and_skips_the_heal_attempt(self):
        worker = _worker(correlation_auto_heal=False)
        with (
            patch("uasr.mapek_worker.persist_recovery_row", new_callable=AsyncMock) as mock_persist,
            patch("uasr.mapek_worker.attempt_cross_source_heal", new_callable=AsyncMock) as mock_heal,
        ):
            mock_persist.return_value = None
            result = await worker._persist_and_maybe_cross_heal(_batch(), _drift(), _failed_recovery())

        assert result is None
        mock_persist.assert_called_once()
        assert mock_persist.call_args.kwargs["return_row"] is False
        mock_heal.assert_not_called()

    @pytest.mark.asyncio
    async def test_feature_on_but_persistence_failed_skips_the_heal_attempt(self):
        worker = _worker(correlation_auto_heal=True)
        with (
            patch("uasr.mapek_worker.persist_recovery_row", new_callable=AsyncMock) as mock_persist,
            patch("uasr.mapek_worker.attempt_cross_source_heal", new_callable=AsyncMock) as mock_heal,
        ):
            mock_persist.return_value = None  # DB hiccup inside persist_recovery_row
            result = await worker._persist_and_maybe_cross_heal(_batch(), _drift(), _failed_recovery())

        assert result is None
        assert mock_persist.call_args.kwargs["return_row"] is True
        mock_heal.assert_not_called()

    @pytest.mark.asyncio
    async def test_feature_on_no_candidate_closes_the_session_and_returns_none(self):
        worker = _worker(correlation_auto_heal=True, correlation_window_seconds=45.0)
        mock_db = AsyncMock()
        mock_rec = MagicMock()
        with (
            patch("uasr.mapek_worker.persist_recovery_row", new_callable=AsyncMock) as mock_persist,
            patch("uasr.mapek_worker.attempt_cross_source_heal", new_callable=AsyncMock) as mock_heal,
        ):
            mock_persist.return_value = (mock_db, mock_rec)
            mock_heal.return_value = None  # no correlated sibling, or it didn't validate
            result = await worker._persist_and_maybe_cross_heal(_batch(), _drift(), _failed_recovery())

        assert result is None
        mock_heal.assert_called_once()
        # tracker/loop/window threaded through from the worker's own config/instances
        args = mock_heal.call_args.args
        assert args[4] is worker._metrics
        assert args[5] is worker._loop
        assert args[6] == 45.0
        mock_db.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_feature_on_successful_borrow_returns_the_healed_result_and_closes_the_session(self):
        worker = _worker(correlation_auto_heal=True)
        mock_db = AsyncMock()
        mock_rec = MagicMock()
        healed = RecoveryLoopResult(
            drift_event_id="batch_001", recovery_id="borrowed_rec",
            status=RecoveryStatus.DEPLOYED,
            shim=ShimResult(
                recovery_id="borrowed_rec", shim_code="return rows",
                generation_method="cross_source_borrowed", validation_passed=True,
            ),
        )
        with (
            patch("uasr.mapek_worker.persist_recovery_row", new_callable=AsyncMock) as mock_persist,
            patch("uasr.mapek_worker.attempt_cross_source_heal", new_callable=AsyncMock) as mock_heal,
        ):
            mock_persist.return_value = (mock_db, mock_rec)
            mock_heal.return_value = healed
            result = await worker._persist_and_maybe_cross_heal(_batch(), _drift(), _failed_recovery())

        assert result is healed
        mock_db.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_session_is_closed_even_when_the_heal_attempt_raises(self):
        """The session must never leak just because attempt_cross_source_heal
        blew up -- same reasoning as any try/finally around a resource."""
        worker = _worker(correlation_auto_heal=True)
        mock_db = AsyncMock()
        mock_rec = MagicMock()
        with (
            patch("uasr.mapek_worker.persist_recovery_row", new_callable=AsyncMock) as mock_persist,
            patch("uasr.mapek_worker.attempt_cross_source_heal", new_callable=AsyncMock) as mock_heal,
        ):
            mock_persist.return_value = (mock_db, mock_rec)
            mock_heal.side_effect = RuntimeError("boom")
            with pytest.raises(RuntimeError):
                await worker._persist_and_maybe_cross_heal(_batch(), _drift(), _failed_recovery())

        mock_db.close.assert_awaited_once()


class TestMakeCanaryTransform:

    def test_wraps_shim_code_as_a_router_transform(self):
        with patch("uasr.mapek_worker.RecoveryLoop") as mock_loop_cls:
            mock_loop_cls._sandbox_execute.return_value = [{"a": 2}]
            transform = _make_canary_transform("return [{'a': 2}]")
            result = transform("s_failing", [{"a": 1}])

        mock_loop_cls._sandbox_execute.assert_called_once_with("return [{'a': 2}]", [{"a": 1}])
        assert result == [{"a": 2}]
