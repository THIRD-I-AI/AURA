"""BUG-275: ConformalMartingaleRegistry is used from two threads -- /uasr/baseline
re-registers a source on one worker thread while the MAPE-K loop's detect step reads it
on another -- and had no lock. register_baseline empties the per-source dicts and
refills them column by column, so a concurrent update() could hit a KeyError on a
baseline that was not there yet."""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.conformal_martingale import ConformalMartingaleRegistry

COLUMNS = [f"c{i}" for i in range(40)]
BASELINE = {c: [float(i) for i in range(30)] for c in COLUMNS}
BATCH = [float(i) + 0.5 for i in range(30)]


def test_reads_never_see_a_half_registered_source():
    registry = ConformalMartingaleRegistry()
    registry.register_baseline("src", BASELINE)
    errors: list = []
    stop = threading.Event()

    def _rebaseline() -> None:
        try:
            for _ in range(60):
                registry.register_baseline("src", BASELINE)
        except Exception as exc:  # pragma: no cover - reported by the assertion
            errors.append(exc)
        finally:
            stop.set()

    def _read() -> None:
        try:
            while not stop.is_set():
                for col in COLUMNS:
                    registry.update("src", col, BATCH)
                    registry.diagnostics("src", col)
        except Exception as exc:  # pragma: no cover - reported by the assertion
            errors.append(exc)

    readers = [threading.Thread(target=_read) for _ in range(4)]
    writer = threading.Thread(target=_rebaseline)
    for t in readers + [writer]:
        t.start()
    for t in readers + [writer]:
        t.join(timeout=60)

    assert errors == []


def test_the_mutators_and_readers_hold_the_registry_lock():
    registry = ConformalMartingaleRegistry()
    registry.register_baseline("src", {"a": [1.0, 2.0, 3.0]})

    # While another thread holds the lock, a reader must wait rather than proceed.
    registry._lock.acquire()
    done = threading.Event()
    reader = threading.Thread(target=lambda: (registry.update("src", "a", [1.5]), done.set()))
    reader.start()
    try:
        assert not done.wait(timeout=0.3), "update() ran without taking the lock"
    finally:
        registry._lock.release()
    reader.join(timeout=5)
    assert done.is_set()
