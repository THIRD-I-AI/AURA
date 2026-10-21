"""Audit round 7, audit-UI area: backend halves of BUG-481 and BUG-482.

BUG-483 and BUG-484 are frontend-only; their regression tests live next to the
code they cover (frontend/src/components/HITL/__tests__/ExceptionQueue.test.tsx
and frontend/src/services/__tests__/ensureAuditorTokenMintRefused.test.ts).
"""
import asyncio
import os
import sys

import pandas as pd
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TENANT = "org-r7"


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """``TENANT``'s upload directory, laid out exactly as the gateway upload writes it."""
    from shared.storage import reset_storage_backend
    from shared.storage.base import tenant_slug

    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    reset_storage_backend()
    d = tmp_path / "uploads" / tenant_slug(TENANT)
    d.mkdir(parents=True)
    yield d
    reset_storage_backend()


# ── BUG-481: names the gateway accepts must be auditable ─────────────

@pytest.mark.parametrize("name", ["Loan Data 2024.csv", "loans (1).csv", "décisions.csv"])
def test_bug481_upload_with_space_or_parens_is_found(uploads, name):
    from api_gateway.routers.files import _safe_upload_name as gateway_name
    from counterfactual_service import main

    # The gateway hands this exact name back to the wizard after upload.
    assert gateway_name(name) == name
    (uploads / name).write_text("t,y\n0,1\n1,0\n", encoding="utf-8")

    assert main._upload_exists(name, TENANT) is True
    assert main._csv_header_columns(name, TENANT) == ["t", "y"]
    df = asyncio.run(main._resolve_dataset(f"uploaded_file:{name}", TENANT))
    assert list(df.columns) == ["t", "y"]


@pytest.mark.parametrize("name", ["../secret.csv", "a/b.csv", "a\\b.csv", "..", ".", "", "x\x00.csv"])
def test_bug481_traversal_names_still_rejected(uploads, name):
    from counterfactual_service import main

    assert main._upload_exists(name, TENANT) is False
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main._resolve_dataset(f"uploaded_file:{name}", TENANT))
    assert ei.value.status_code == 404


# ── BUG-482: the header the wizard offers is the header the server checks ─

def test_bug482_header_whitespace_matches_wizard_columns(uploads):
    from counterfactual_service import main

    (uploads / "spaced.csv").write_text(
        "id, loan_amount, approved\n1, 100, 1\n2, 250, 0\n", encoding="utf-8")

    # The wizard's preview (frontend/src/audit/csv.ts) trims each header field,
    # and so does DuckDB's read_csv_auto, which the chat pipeline uses.
    assert main._csv_header_columns("spaced.csv", TENANT) == ["id", "loan_amount", "approved"]
    df = asyncio.run(main._resolve_dataset("uploaded_file:spaced.csv", TENANT))
    assert list(df.columns) == ["id", "loan_amount", "approved"]
    assert df["loan_amount"].tolist() == [100, 250]


def test_bug482_duckdb_parity(uploads):
    duckdb = pytest.importorskip("duckdb")
    from counterfactual_service import main

    path = uploads / "spaced.csv"
    path.write_text("a , b,c \n1,2,3\n", encoding="utf-8")
    duck_cols = duckdb.sql(f"select * from read_csv_auto('{path.as_posix()}')").columns
    assert main._csv_header_columns("spaced.csv", TENANT) == list(duck_cols)


def test_bug482_untrimmed_headers_unchanged(uploads):
    from counterfactual_service import main

    (uploads / "plain.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert main._csv_header_columns("plain.csv", TENANT) == ["a", "b"]
    df = main._read_upload("plain.csv", TENANT)
    assert isinstance(df, pd.DataFrame) and df.shape == (1, 2)
