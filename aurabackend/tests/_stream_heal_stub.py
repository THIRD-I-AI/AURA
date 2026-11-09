"""A heal step that passes every micro-batch through, for streaming tests about
something other than healing (lifecycle, windows, sinks, BUG-569 health).

ADR-002 phase 2b makes the engine fail closed: with no UASR to answer, no event
reaches a sink. Tests of the engine's other behaviour install this so they keep
exercising it without a UASR; tests/test_heal_phase2b_streaming.py drives the real
/uasr/heal handler instead. The quarantine file goes under ``tmp_path``.
"""
from __future__ import annotations

from typing import List

from pipeline.streaming import path_confinement
from pipeline.streaming import streaming_engine as engine_mod
from pipeline.streaming.heal import HealOutcome
from pipeline.streaming.models import StreamEvent


class PassThroughHealer:
    needs_baseline = False

    def __init__(self, _pipeline=None):
        self.batches: List[List[StreamEvent]] = []

    async def heal(self, events: List[StreamEvent]) -> HealOutcome:
        self.batches.append(list(events))
        return HealOutcome(events=list(events))

    async def aclose(self) -> None:
        return None


def install(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(engine_mod, "_create_healer", PassThroughHealer)
    monkeypatch.setattr(path_confinement, "STREAMING_OUTPUT_ROOT", str(tmp_path / "streaming_output"))
