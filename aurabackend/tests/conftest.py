"""
AURA Test Configuration
========================
Shared fixtures and path setup for all test modules.
"""

import os
import sys

# Force matplotlib's headless backend before any test imports DoWhy/matplotlib.
# DoWhy's refuters call plt.show(), which opens a blocking GUI window on a dev
# machine with a display and hangs the suite (CI is headless so it never hit
# this). Must run at conftest import, before test modules are collected.
os.environ.setdefault("MPLBACKEND", "Agg")

# Point the API gateway at a throwaway SQLite DB for the whole test session
# so tests never write the committed dev DB (data/gateway.db). Under WSL,
# writing that tracked file on the /mnt/c drvfs mount also fails with
# "readonly database"; an ext4 temp dir avoids both the hygiene problem and
# the WSL limitation. setdefault preserves an explicit override (e.g. CI
# pointing at Postgres).
import tempfile as _tempfile
_gateway_test_db = os.path.join(_tempfile.gettempdir(), "aura_gateway_test.db")
os.environ.setdefault(
    "GATEWAY_DATABASE_URL", f"sqlite+aiosqlite:///{_gateway_test_db}"
)

import pytest

# Add aurabackend to sys.path so tests can import modules directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _reset_storage_backend_singleton():
    """S45: the storage backend is a process-wide singleton cached by kind and
    reads AURA_UPLOADS_ROOT at construction. Tests that monkeypatch the root or
    AURA_STORAGE_BACKEND would otherwise leak a stale backend into later tests
    (e.g. test_e2e_chat reading an earlier test's tmp dir). Reset around every
    test so each re-reads its own environment."""
    try:
        from shared.storage import reset_storage_backend
        reset_storage_backend()
    except Exception:
        pass
    yield
    try:
        from shared.storage import reset_storage_backend
        reset_storage_backend()
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    """Shut down joblib/loky's reusable process pool explicitly.

    Root-caused via a thread-dump probe: after "1791 passed" the interpreter
    sometimes hangs in threading._shutdown joining loky's NON-DAEMON
    ExecutorManagerThread (spawned by sklearn/econml n_jobs work; loky's own
    atexit hook loses a race under load — the cause of the pre-push gate's
    exit-hang). Killing the pool here removes the thread before exit.
    """
    try:
        from joblib.externals.loky import get_reusable_executor
        get_reusable_executor().shutdown(kill_workers=True)
    except Exception:
        pass
