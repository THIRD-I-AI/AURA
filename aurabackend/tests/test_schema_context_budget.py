"""BUG-444: a chat prompt's schema context stays within budget however many files a user has."""
from __future__ import annotations

from shared.data_utils import _format_context_for_llm, select_tables_within_budget


def _table(cols):
    return {
        "columns": [{"name": c, "type": "VARCHAR"} for c in cols],
        "row_count": 100,
        "sample_data": [{c: f"{c}-value-{i}" for c in cols} for i in range(3)],
    }


def _account(n: int = 72) -> dict:
    tables = {f"verify_{i:04d}_upload": _table([f"metric_{i}_{j}" for j in range(12)]) for i in range(n)}
    tables["sales_2025"] = _table(["region", "revenue", "order_date", "customer_id"])
    tables["customers"] = _table(["customer_id", "name", "segment"])
    return tables


_RELS = [{"from_table": "sales_2025", "from_column": "customer_id", "to_table": "customers",
          "to_column": "customer_id", "overlap_count": 90}]


def test_the_whole_account_is_far_over_budget():
    # The precondition the bug needed: every table together is far too large to send.
    assert len(_format_context_for_llm(_account(), _RELS)) > 40_000


def test_a_question_that_names_no_table_still_fits_and_keeps_the_relevant_one():
    tables, rels, text = select_tables_within_budget(
        _account(), _RELS, "What is the total revenue by region?", budget_chars=16_000)
    assert len(text) <= 16_000
    assert "sales_2025" in tables
    assert len(tables) < len(_account())


def test_a_named_table_and_its_related_tables_come_first():
    tables, rels, text = select_tables_within_budget(
        _account(), _RELS, "join them", budget_chars=16_000, focus="sales_2025")
    assert list(tables)[:2] == ["sales_2025", "customers"]
    assert rels == _RELS and "JOIN these tables" in text


def test_at_least_one_table_is_kept_even_if_it_alone_is_over_budget():
    huge = {"wide": _table([f"c{i}" for i in range(400)])}
    tables, _, text = select_tables_within_budget(huge, [], "anything", budget_chars=500)
    assert list(tables) == ["wide"] and text


def test_a_small_account_is_sent_whole():
    small = {k: v for k, v in _account(2).items()}
    tables, _, _ = select_tables_within_budget(small, _RELS, "hello", budget_chars=16_000)
    assert set(tables) == set(small)
