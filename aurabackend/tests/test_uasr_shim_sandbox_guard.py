"""BUG-258 / BUG-261: the shim "sandbox" injected the real `logging` module into the
exec namespace, and nothing checked the source -- so LLM-generated shim code, which runs
in-process BEFORE any human approves it, could reach the filesystem, the environment
and subprocesses. The source is now checked statically and `logging` is a stand-in."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.recovery_loop import RecoveryLoop

ROWS = [{"v": 1}, {"v": 2}]


def _shim(body: str) -> str:
    return f"def transform(rows):\n{body}\n    return rows\n"


@pytest.mark.parametrize("body", [
    # the route the audit found: the real logging module carries os and sys
    "    logging.os.environ.get('SECRET')",
    "    logging.sys.modules",
    # dunder walks back to full builtins
    "    ().__class__.__mro__[1].__subclasses__()",
    "    transform.__globals__",
    "    x = __builtins__",
    "    __import__('os')",
    # frame / generator introspection reaches the caller's globals without a dunder
    "    g = (r for r in rows)\n    g.gi_frame.f_back",
    # str.format traverses attributes without an Attribute node
    "    '{0.__class__}'.format(rows)",
    # imports
    "    import os",
    "    from os import path",
], ids=["logging.os", "logging.sys", "dunder-mro", "dunder-globals", "builtins-name",
        "dunder-import", "frame-walk", "format-string", "import-os", "from-import"])
def test_shim_that_tries_to_leave_the_namespace_is_rejected(body, tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET", "do-not-leak")
    with pytest.raises((ValueError, ImportError, AttributeError)):
        RecoveryLoop._sandbox_execute(_shim(body), ROWS)


def test_the_logging_stand_in_exposes_nothing_but_logging():
    from uasr.recovery_loop import _shim_logging

    stand_in = _shim_logging()
    assert not hasattr(stand_in, "os") and not hasattr(stand_in, "sys")
    logger = stand_in.getLogger("uasr.shim.test")
    assert not hasattr(logger, "handlers") and not hasattr(logger, "manager")
    logger.warning("drift in %s", ["a"])  # does not raise


def test_ordinary_shims_still_run(caplog):
    # module constant + the injected logging (how the templates are written)
    monitor = (
        '"""UASR Shim - Drift Monitor"""\n\n'
        '_logger = logging.getLogger("uasr.shim.monitor")\n'
        "_CLIP_MAX = 1\n\n"
        "def transform(rows: list[dict]) -> list[dict]:\n"
        '    """Clip and log."""\n'
        '    _logger.warning("clipping %s rows", len(rows))\n'
        "    return [{**r, 'v': min(r['v'], _CLIP_MAX)} for r in rows]\n"
    )
    with caplog.at_level("WARNING", logger="uasr.shim.monitor"):
        assert RecoveryLoop._sandbox_execute(monitor, ROWS) == [{"v": 1}, {"v": 1}]
    assert "clipping 2 rows" in caplog.text

    # the fallback template carries its own `import logging`
    fallback = (
        "import logging\n"
        '_logger = logging.getLogger("uasr.shim.fallback")\n\n'
        "def transform(rows):\n"
        '    _logger.warning("fallback active")\n'
        "    return rows\n"
    )
    assert RecoveryLoop._sandbox_execute(fallback, ROWS) == ROWS


def test_input_rows_are_not_mutated():
    rows = [{"v": 1}]
    RecoveryLoop._sandbox_execute("def transform(rows):\n    rows[0]['v'] = 99\n    return rows\n", rows)
    assert rows == [{"v": 1}]


@pytest.mark.parametrize("body", [
    # BUG-343: no single literal holds `__`, so the per-Constant check let these through,
    # and str.format then walked get_logger.__globals__ to the real logging/os modules.
    "    rows[0]['leak'] = ('{0._' + '_globals_' + '_[logging].os.environ[SECRET]}').format(logging.getLogger)",
    "    rows[0]['leak'] = str.format('{0._' + '_globals_' + '_[logging].os.environ[SECRET]}', logging.getLogger)",
    "    rows[0]['leak'] = ('{f._' + '_globals_' + '_[logging].os.environ[SECRET]}').format_map({'f': logging.getLogger})",
], ids=["format", "str.format", "format_map"])
def test_a_format_string_assembled_at_runtime_cannot_reach_the_environment(body, monkeypatch):
    monkeypatch.setenv("SECRET", "do-not-leak")
    try:
        result = RecoveryLoop._sandbox_execute(_shim(body), ROWS)
    except (ValueError, ImportError, AttributeError):
        return
    assert "do-not-leak" not in repr(result)
    pytest.fail(f"shim ran: {result!r}")


@pytest.mark.asyncio
async def test_a_deployed_shim_that_runs_long_on_a_later_batch_is_time_limited_and_suspended():
    # BUG-348: only validation was time-limited. A deployed shim whose runtime depends
    # on the data (here: it loops `n` times) ran unbounded on every later batch.
    # (Bounded here so the stray thread finishes; a real runaway would never return.)
    import asyncio

    from uasr.recovery_loop import RecoveryLoopConfig

    loop = RecoveryLoop(detector=None, config=RecoveryLoopConfig(sandbox_timeout_seconds=0.2))
    shim = (
        "def transform(rows):\n"
        "    for r in rows:\n"
        "        for _ in range(r['n']):\n"
        "            pass\n"
        "    return rows\n"
    )
    loop.hydrate_deployed_shims({"s": [shim]})
    assert (await loop.apply_shims_counted_async("s", [{"n": 10}]))[1] == 1

    started = asyncio.get_running_loop().time()
    rows, applied, total = await loop.apply_shims_counted_async("s", [{"n": 30_000_000}])
    assert (applied, total) == (0, 1) and rows == [{"n": 30_000_000}]
    assert asyncio.get_running_loop().time() - started < 1.0

    # suspended: the next batch fails at once instead of tying up another thread
    started = asyncio.get_running_loop().time()
    _, applied, _ = await loop.apply_shims_counted_async("s", [{"n": 1}])
    assert applied == 0 and asyncio.get_running_loop().time() - started < 0.1
