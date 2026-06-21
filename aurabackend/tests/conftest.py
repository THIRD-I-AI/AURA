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
