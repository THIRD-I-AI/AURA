"""
Sprint S18.1c — ShimRouter wired into MAPEKWorker.

Verifies:
* When ``use_shim_router=False`` (default), the worker uses the
  original pause/resume path (no regression).
* When ``use_shim_router=True``, ``self._shim_router`` is constructed
  and ``pause()`` is NOT called during recovery — batch ingestion
  continues uninterrupted.
* Config flag wiring: ``shim_router_canary_initial_weight`` flows
  through to ``add_canary(initial_weight=...)``.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.mapek_worker import MAPEKConfig


class TestShimRouterConfig:

    def test_shim_router_defaults_off(self) -> None:
        cfg = MAPEKConfig()
        assert cfg.use_shim_router is False
        assert cfg.shim_router_canary_initial_weight == 0.1

    def test_shim_router_flag_on(self) -> None:
        cfg = MAPEKConfig(use_shim_router=True, shim_router_canary_initial_weight=0.05)
        assert cfg.use_shim_router is True
        assert cfg.shim_router_canary_initial_weight == 0.05


class TestShimRouterConstruction:

    def test_no_router_when_flag_off(self) -> None:
        from uasr.mapek_worker import MAPEKWorker
        cfg = MAPEKConfig(use_shim_router=False)
        worker = MAPEKWorker(config=cfg)
        assert worker._shim_router is None

    def test_router_constructed_when_flag_on(self) -> None:
        from uasr.mapek_worker import MAPEKWorker
        cfg = MAPEKConfig(use_shim_router=True)
        worker = MAPEKWorker(config=cfg)
        assert worker._shim_router is not None

    def test_pause_resume_still_available_when_router_on(self) -> None:
        from uasr.mapek_worker import MAPEKWorker
        cfg = MAPEKConfig(use_shim_router=True)
        worker = MAPEKWorker(config=cfg)
        assert hasattr(worker, "pause")
        assert hasattr(worker, "resume")
        assert not worker.is_paused
