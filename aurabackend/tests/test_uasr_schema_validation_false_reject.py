"""
Schema-validation false-reject fix.

Root-caused live against a staged deployment (2026-08-31,
docs/superpowers/specs/2026-08-31-uasr-live-validation-and-benchmark.md):
a correct schema-rename shim was silently rejected whenever the corrected
batch also carried unrelated statistical drift -- a real-world-common
co-occurrence (a schema migration and ordinary data variation landing in
the same batch), not a coincidence unique to a bad test.

``RecoveryLoop._validate_shim`` (aurabackend/uasr/recovery_loop.py) checks
schema drift first (drift_detector.py resolves schema drift before
statistical), so `post_drift.drift_type` can legitimately come back
STATISTICAL even after a shim correctly fixes SCHEMA drift. The pre-fix
condition (`drift.drift_type == SCHEMA and not post_drift.drift_detected`)
required ALL drift, of any type, to be gone -- provably dead code, since
reaching that line already required `post_drift.drift_detected` to be
True. The fix checks specifically that the residual drift, if any, is no
longer classified as SCHEMA.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.models import (
    BatchPayload,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    ShimResult,
)
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig

_RENAME_SHIM = (
    'def transform(rows: list[dict]) -> list[dict]:\n'
    '    result = []\n'
    '    for row in rows:\n'
    '        mapped = {}\n'
    '        for k, v in row.items():\n'
    '            mapped["amount" if k == "total_amount" else k] = v\n'
    '        result.append(mapped)\n'
    '    return result\n'
)


def _schema_drift() -> DriftDetectionResult:
    return DriftDetectionResult(
        source_id="s1", batch_id="b1", drift_detected=True,
        drift_type=DriftType.SCHEMA, severity=DriftSeverity.HIGH,
        kl_divergence=None,  # schema drift carries no KL value, as in production
    )


def _batch() -> BatchPayload:
    return BatchPayload(
        source_id="s1", batch_id="b1",
        rows=[{"user_id": 1, "total_amount": 10.5, "status": "active"}],
    )


def _shim() -> ShimResult:
    return ShimResult(recovery_id="r1", shim_code=_RENAME_SHIM)


def _loop_with_post_drift(post_drift: DriftDetectionResult) -> RecoveryLoop:
    detector = MagicMock()
    detector.detect.return_value = post_drift
    return RecoveryLoop(detector=detector, config=RecoveryLoopConfig())


class TestSchemaValidationDoesNotFalseReject:

    @pytest.mark.asyncio
    async def test_passes_when_schema_and_everything_else_is_clean(self):
        """The already-working case (line 339's check) must keep working."""
        loop = _loop_with_post_drift(DriftDetectionResult(
            source_id="s1", batch_id="b1_shimmed", drift_detected=False,
        ))
        result = await loop._validate_shim(_shim(), _batch(), _schema_drift())
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_passes_when_schema_resolved_but_unrelated_statistical_drift_remains(self):
        """The bug: a schema shim that correctly fixed the schema was
        rejected because the corrected batch also showed incidental
        statistical drift -- a different, unrelated problem."""
        loop = _loop_with_post_drift(DriftDetectionResult(
            source_id="s1", batch_id="b1_shimmed", drift_detected=True,
            drift_type=DriftType.STATISTICAL, severity=DriftSeverity.LOW,
            kl_divergence=0.9163,
        ))
        result = await loop._validate_shim(_shim(), _batch(), _schema_drift())
        assert result["passed"] is True
        assert "Schema drift resolved" in result["reason"]

    @pytest.mark.asyncio
    async def test_still_fails_when_schema_drift_itself_persists(self):
        """The shim must still be rejected if it did NOT fix the schema --
        this fix must not turn validation into a rubber stamp."""
        loop = _loop_with_post_drift(DriftDetectionResult(
            source_id="s1", batch_id="b1_shimmed", drift_detected=True,
            drift_type=DriftType.SCHEMA, severity=DriftSeverity.HIGH,
        ))
        result = await loop._validate_shim(_shim(), _batch(), _schema_drift())
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_non_schema_original_drift_is_unaffected(self):
        """This fix only changes the SCHEMA branch -- a statistical-origin
        drift that isn't sufficiently reduced must still fail exactly as
        before."""
        loop = _loop_with_post_drift(DriftDetectionResult(
            source_id="s1", batch_id="b1_shimmed", drift_detected=True,
            drift_type=DriftType.STATISTICAL, severity=DriftSeverity.LOW,
            kl_divergence=0.5,
        ))
        original = DriftDetectionResult(
            source_id="s1", batch_id="b1", drift_detected=True,
            drift_type=DriftType.STATISTICAL, severity=DriftSeverity.HIGH,
            kl_divergence=0.6,
        )
        result = await loop._validate_shim(_shim(), _batch(), original)
        assert result["passed"] is False
