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


def test_local_storage_lists_xlsx_and_schema_context_includes_it(tmp_path, monkeypatch):
    """BUG-176: LocalBackend.list filtered on .csv/.parquet/.json, so a stored workbook
    never reached build_schema_context even though smart_load_file could read it."""
    from shared import storage
    from shared.storage.local import LocalBackend

    src = tmp_path / "src.xlsx"
    pd.DataFrame({"region": ["e", "w"], "amount": [1, 2]}).to_excel(src, index=False)
    backend = LocalBackend(str(tmp_path / "store"))
    backend.write("tenant-a", "sales.xlsx", src.read_bytes())

    assert [o.name for o in backend.list("tenant-a")] == ["sales.xlsx"]

    monkeypatch.setattr(storage, "get_storage_backend", lambda: backend)
    ctx = data_utils.build_schema_context(duckdb.connect(":memory:"), "tenant-a", use_llm=False)
    assert "sales" in ctx["tables"]
    assert [c["name"] for c in ctx["tables"]["sales"]["columns"]] == ["region", "amount"]


@pytest.mark.asyncio
async def test_schema_signature_is_computed_off_the_event_loop(monkeypatch):
    """BUG-177: build_schema_context_cached listed storage on the loop thread, which on S3
    is blocking network I/O in a single-worker gateway."""
    import threading

    seen = {}

    def fake_signature(tenant):
        seen["thread"] = threading.get_ident()
        return ""  # empty signature -> the function returns early, no cache/DB needed

    monkeypatch.setattr(data_utils, "_signature_for_tenant", fake_signature)
    out = await data_utils.build_schema_context_cached(duckdb.connect(":memory:"), "t", use_llm=False)
    assert out["tables"] == {}
    assert seen["thread"] != threading.get_ident(), "signature ran on the event-loop thread"


def test_files_that_sanitise_to_the_same_table_name_do_not_overwrite_each_other(tmp_path, monkeypatch):
    """BUG-178: 'q1-sales.csv' / 'q1_sales.csv' and 'sales.csv' / 'sales.parquet' shared one table."""
    from shared import storage
    from shared.storage.local import LocalBackend

    backend = LocalBackend(str(tmp_path / "store"))
    backend.write("t", "q1-sales.csv", b"a,b\n1,2\n")
    backend.write("t", "q1_sales.csv", b"a,b\n3,4\n5,6\n")
    pd.DataFrame({"x": [1]}).to_parquet(tmp_path / "s.parquet")
    backend.write("t", "sales.parquet", (tmp_path / "s.parquet").read_bytes())
    backend.write("t", "sales.csv", b"y\n1\n2\n3\n")

    monkeypatch.setattr(storage, "get_storage_backend", lambda: backend)
    ctx = data_utils.build_schema_context(duckdb.connect(":memory:"), "t", use_llm=False)

    rows = {name: info["row_count"] for name, info in ctx["tables"].items()}
    assert len(rows) == 4, f"a file was silently replaced: {rows}"
    assert sorted(rows.values()) == [1, 1, 2, 3]


def test_unique_table_name_keeps_the_plain_name_for_the_first_file():
    assert data_utils._unique_table_name("orders.csv", {}) == "orders"
    assert data_utils._unique_table_name("orders.parquet", {"orders": 1}) == "orders_parquet"
    assert data_utils._unique_table_name("orders.parquet", {"orders": 1, "orders_parquet": 1}) == "orders_2"


def test_xlsx_that_expands_beyond_the_limit_is_refused_before_parsing(xlsx, monkeypatch):
    """BUG-180: only the compressed size was checked, so a small zip could expand enormously."""
    monkeypatch.setattr(data_utils, "MAX_EXCEL_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(ValueError, match="expands to"):
        smart_load_file(duckdb.connect(":memory:"), xlsx, "sales", use_llm=False)


def test_xlsx_with_too_many_rows_is_refused(xlsx, monkeypatch):
    monkeypatch.setattr(data_utils, "MAX_EXCEL_ROWS", 2)  # the fixture sheet has 3 data rows
    with pytest.raises(ValueError, match="more than"):
        smart_load_file(duckdb.connect(":memory:"), xlsx, "sales", use_llm=False)


def test_a_non_workbook_named_xlsx_gets_a_clear_error(tmp_path):
    bad = tmp_path / "fake.xlsx"
    bad.write_bytes(b"not a zip at all")
    with pytest.raises(ValueError, match="valid .xlsx"):
        smart_load_file(duckdb.connect(":memory:"), str(bad).replace("\\", "/"), "fake", use_llm=False)
