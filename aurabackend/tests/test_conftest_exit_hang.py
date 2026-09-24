"""BUG-164 regression: a leaked aiosqlite connection must not hang pytest at exit.

aiosqlite parks every connection on a NON-DAEMON worker thread, so any
connection still open when the interpreter starts shutting down blocks
threading._shutdown forever -- the suite prints "N passed" and never returns,
which the pre-push hook sees as a hung push. conftest.pytest_sessionfinish
stops every surviving connection; this test proves it against a child pytest
that leaks one on purpose.
"""
import os
import shutil
import subprocess
import sys

_LEAKY_TEST = '''
import asyncio

import aiosqlite

_leaked = []


def test_leaks_an_open_aiosqlite_connection():
    async def open_and_forget():
        _leaked.append(await aiosqlite.connect(":memory:"))

    asyncio.run(open_and_forget())
'''


def test_leaked_aiosqlite_connection_does_not_hang_pytest_exit(tmp_path):
    shutil.copy(os.path.join(os.path.dirname(__file__), "conftest.py"), tmp_path / "conftest.py")
    (tmp_path / "test_leak.py").write_text(_LEAKY_TEST)

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            "child pytest hung at exit after its tests passed -- a leaked "
            f"aiosqlite worker thread is blocking shutdown:\n{exc.stdout or ''}"
        ) from exc

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 passed" in proc.stdout
