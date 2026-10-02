"""BUG-311: the SQL expression guard removed quoted spans with a regex that knows
nothing about comments, so a quote hidden in a block comment blanked out the text the
guard was supposed to inspect."""
from __future__ import annotations

import pytest

from shared.sql_expression_guard import validate_sql_expression


@pytest.mark.parametrize("expr", [
    "amount > 0 /*'*/ ; DROP TABLE t; -- /*'*/",
    "id IN (SELECT column0 FROM /*'*/ read_csv_auto('/etc/passwd') /*'*/)",
    'amount > 0 /*"*/ ; ATTACH \'x.db\'; -- /*"*/',
    "amount > 0 -- ' \n ; DROP TABLE t; -- '",
    "amount > 0 /* harmless */",
    "amount > 0 -- trailing note",
])
def test_an_expression_containing_a_comment_is_refused(expr):
    with pytest.raises(ValueError):
        validate_sql_expression(expr)


@pytest.mark.parametrize("expr", ["name = 'unterminated", 'x = "open'])
def test_an_unterminated_quote_is_refused(expr):
    with pytest.raises(ValueError, match="unterminated"):
        validate_sql_expression(expr)


@pytest.mark.parametrize("expr", [
    "amount > 100 AND region = 'EU'",
    "note = 'a; b'",                       # a semicolon inside a string is data
    "note = 'see /* this */ and -- that'",  # comment markers inside a string are data
    "label = 'it''s fine'",
    '"Order Date" >= DATE \'2026-01-01\'',
    "price * 1.2",
    "a - b",
    "CASE WHEN x > 0 THEN 'pos' ELSE 'neg' END",
])
def test_ordinary_expressions_still_pass(expr):
    validate_sql_expression(expr)
