"""BUG-438: the row cap must stay a working clause whatever the query ends with."""
from __future__ import annotations

import duckdb
import pytest

from connectors.sql_limit import with_row_limit


@pytest.mark.parametrize("query", [
    "SELECT v FROM t;",
    "SELECT v FROM t ;  \n",
    "SELECT v FROM t -- every row",
    "SELECT v FROM t; -- every row",
    "SELECT v FROM t /* every row */",
    "SELECT v FROM t\n-- note one\n-- note two\n",
])
def test_the_cap_applies_after_trailing_semicolons_and_comments(query):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT range AS v FROM range(25)")
    assert len(con.execute(with_row_limit(query, 10)).fetchall()) == 10


@pytest.mark.parametrize("query", [
    "SELECT v FROM t LIMIT 3;",
    "SELECT v FROM t LIMIT 3 -- already capped",
])
def test_an_existing_limit_is_kept_and_not_doubled(query):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT range AS v FROM range(25)")
    assert len(con.execute(with_row_limit(query, 10)).fetchall()) == 3


def test_dashes_inside_a_string_literal_are_not_treated_as_a_comment():
    capped = with_row_limit("SELECT * FROM t WHERE note = 'a--b'", 10)
    assert capped == "SELECT * FROM t WHERE note = 'a--b' LIMIT 10"
