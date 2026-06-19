import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.duckdb_factory import new_connection


def test_new_connection_local_is_usable(monkeypatch):
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend
    reset_storage_backend()
    con = new_connection()
    assert con.execute("SELECT 42").fetchone() == (42,)


def test_new_connection_configures_backend():
    fake = MagicMock()
    with patch("shared.duckdb_factory.get_storage_backend", return_value=fake):
        con = new_connection()
    fake.configure_duckdb.assert_called_once()
    assert con.execute("SELECT 1").fetchone() == (1,)
