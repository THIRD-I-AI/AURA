"""BUG-146: .xlsx uploads were advertised as queryable but parsed as CSV / skipped by the schema context."""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from shared import data_utils
from shared.data_utils import EXCEL_READ_FN, _replay_tables, smart_load_file


@pytest.fixture()
def xlsx(tmp_path):
    p = tmp_path / "sales.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        pd.DataFrame({"region": ["east", "west", "east"], "amount": [10, 20, 5]}).to_excel(w, sheet_name="Q1", index=False)
        pd.DataFrame({"other": [1]}).to_excel(w, sheet_name="Ignored", index=False)
    return str(p).replace("\\", "/")


def test_smart_load_file_reads_the_first_sheet_of_an_xlsx(xlsx):
    conn = duckdb.connect(":memory:")
    info = smart_load_file(conn, xlsx, "sales", use_llm=False)
    assert [c["name"] for c in info["columns"]] == ["region", "amount"]
    assert info["row_count"] == 3
    assert info["sample_data"][0] == {"region": "east", "amount": 10}
    assert conn.execute("SELECT SUM(amount) FROM sales WHERE region='east'").fetchone()[0] == 15


def test_xlsx_is_in_the_schema_context_allowlists_and_replay_recipe(xlsx):
    assert ".xlsx" in data_utils.EXCEL_EXTENSIONS
    assert data_utils._READ_FN_BY_EXT[".xlsx"] == EXCEL_READ_FN
    conn = duckdb.connect(":memory:")
    _replay_tables(conn, [{"table_name": "sales", "duckdb_uri": xlsx, "read_fn": EXCEL_READ_FN, "renames": []}])
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 3


def test_remote_excel_fails_loudly_instead_of_parsing_binary_as_csv():
    conn = duckdb.connect(":memory:")
    with pytest.raises(ValueError, match="local storage"):
        smart_load_file(conn, "s3://bucket/x.xlsx", "x", use_llm=False)


def test_supported_formats_only_advertise_what_loads():
    from api_gateway.routers.files import get_supported_formats

    fmts = get_supported_formats()["supported_formats"]
    assert fmts["excel"]["extensions"] == [".xlsx"]
    assert "text" not in fmts
