"""Audit round 6 (chat area): BUG-472, BUG-473, BUG-474."""
from __future__ import annotations

import os
import sys
import threading

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.data_utils as du
from agents.dpc_verifier import safe_eval_pandas
from agents.specialists.sql_generator_agent import SQLGeneratorAgent
from api_gateway.routers import chat as chatmod
from shared.cache import schema_cache

# ── BUG-472: string dispatch reached to_csv & co. past the AST allowlist ──────

@pytest.mark.parametrize("expr", [
    'df.apply("to_csv", args=[{path!r}], index=False)',
    'df.agg("to_csv", {path!r})',
    'df.aggregate(func="to_pickle", path={path!r})',
    'df.transform(["to_csv"], {path!r})',
    'df["a"].apply("to_csv", args=[{path!r}])',
    'df.groupby("a").agg(x=("a", "to_csv"))',
    'df.agg("t" + "o_csv", {path!r})',
    'df.agg(df.columns[0], {path!r})',
    'df.pivot_table(index="a", values="a", aggfunc="to_csv")',
    'max([{path!r}], key=df.agg)',
    'df.agg(None, x=("a", "to_csv"))',
    'df["a"].agg(None, x="to_csv")',
    'df["a"].agg(None, x="__class__")',
    'df.agg(None, x=("a", df.columns[0]))',
    'df["a"].map("to_csv")',
    'df["a"].map(df.columns[0])',
    # Second review: keywords pandas reads as named-aggregation specs.
    'df.agg(None, x=("a", "sum"), args=("a", "to_csv"))',
    'df.agg(None, x=("a", "sum"), raw=("a", "to_json"))',
    'df.aggregate(None, x=("a", "sum"), result_type=("a", "to_csv"))',
    'df["a"].agg(None, x="sum", na_action="to_dict")',
    'df["a"].agg(None, x="sum", args="to_csv")',
    'df.agg("sum", args=(df.columns[0],))',
    'df.agg(**{{"func": "to_csv"}})',
    'df.plot(backend="this")',
    'df["a"].hist(backend="os")',
])
def test_string_dispatch_cannot_reach_io_methods(tmp_path, expr):
    target = tmp_path / "pwned.csv"
    df = pd.DataFrame({"a": [1, 2]})
    with pytest.raises(Exception):
        safe_eval_pandas(expr.format(path=str(target)), df)
    assert not target.exists()


@pytest.mark.parametrize("expr, expected", [
    ('df["a"].agg("sum")', 3),
    ('df.agg({"a": "max"})["a"]', 2),
    ('df.groupby("g").agg(total=("a", "sum"))["total"].sum()', 3),
    ('df["a"].apply(abs).sum()', 3),
    ('df["a"].transform("cumsum").iloc[-1]', 3),
    ('df["a"].map({1: "x", 2: "y"}).tolist()', ["x", "y"]),
    ('df["a"].agg(total="sum")["total"]', 3),
    ('df.agg("sum", numeric_only=True)["a"]', 3),
    ('df.pivot_table(values="a", index="g", aggfunc="sum")["a"].sum()', 3),
])
def test_ordinary_aggregations_still_evaluate(expr, expected):
    df = pd.DataFrame({"a": [1, 2], "g": ["x", "y"]})
    assert safe_eval_pandas(expr, df) == expected


# ── BUG-473: the request's file must map to the table the catalog gave it ─────

@pytest.fixture()
async def colliding_uploads(tmp_path, monkeypatch):
    from shared.storage.local import LocalBackend

    backend = LocalBackend(str(tmp_path))
    monkeypatch.setattr("shared.storage.get_storage_backend", lambda: backend)
    folder = tmp_path / backend.tenant_dir("acme").name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sales.csv").write_text("q,amount\n1,10.5\n1,20.0\n")
    pd.DataFrame({"q": [2, 2], "amount": [30.0, 40.0]}).to_parquet(folder / "sales.parquet")
    await schema_cache.clear_prefix(du.SCHEMA_CACHE_PREFIX)
    yield
    await schema_cache.clear_prefix(du.SCHEMA_CACHE_PREFIX)


async def test_audit_target_is_the_collision_suffixed_table(colliding_uploads):
    tables = (await du.load_catalog("acme", use_llm=False))["tables"]
    assert {"sales", "sales_parquet"} <= set(tables)
    assert chatmod._pick_audit_table("sales.parquet", "audit this file", tables) == "sales_parquet"
    assert chatmod._pick_audit_table("sales.csv", "audit this file", tables) == "sales"


async def test_focus_table_is_the_collision_suffixed_table(colliding_uploads):
    tables = (await du.load_catalog("acme", use_llm=False))["tables"]
    assert chatmod._table_for_upload("sales.parquet", tables) == "sales_parquet"
    assert chatmod._table_for_upload("sales.csv", tables) == "sales"
    assert chatmod._table_for_upload("missing.csv", tables) is None


# ── BUG-474: is_available() is a network probe for Ollama; keep it off the loop ─

class _ProbeLLM:
    def __init__(self, available: bool, text: str = "SELECT 1"):
        self.available = available
        self.text = text
        self.probe_threads: list = []

    def is_available(self) -> bool:
        self.probe_threads.append(threading.current_thread())
        return self.available

    def generate(self, prompt):
        return self.text


async def test_availability_probes_run_off_the_event_loop():
    loop_thread = threading.current_thread()
    agent = SQLGeneratorAgent(tool_registry=None)
    agent._llm = _ProbeLLM(available=True)

    assert (await agent._generate_sql("q", "{}"))[0] == "SELECT 1"
    await agent._explain_sql("SELECT 1")
    assert len(agent._llm.probe_threads) == 2
    assert all(t is not loop_thread for t in agent._llm.probe_threads)


async def test_failure_path_probe_runs_off_the_event_loop():
    from agents.base import AgentContext

    loop_thread = threading.current_thread()
    agent = SQLGeneratorAgent(tool_registry=None)
    agent._llm = _ProbeLLM(available=False)

    result = await agent.execute(AgentContext(user_prompt="how many rows?", task_description="how many rows?"))

    assert not result.succeeded
    assert "No LLM provider available" in (result.error or "")
    assert agent._llm.probe_threads
    assert all(t is not loop_thread for t in agent._llm.probe_threads)


# ── BUG-479: expressions sized by a literal or by the data must not exhaust memory ──

@pytest.mark.parametrize("expr", [
    'df["a"].repeat(80000000)',
    'df.sample(n=120000000, replace=True)',
    'df["g"] * 600000000',
    '10**300000000',
    'df["a"].values.repeat(10**9)',
    'df.index.repeat(10**9)',
    'df["g"].str.repeat(10**9)',
    'df.merge(df, how="cross")',
    'df["g"] * 10**9',
    '(10**64)**64',
    '1 << 10**9',
    '[1] * 100000000',
    'df["a"].explode()',
])
def test_size_amplifying_expressions_are_refused(expr):
    with pytest.raises(ValueError):
        safe_eval_pandas(expr, pd.DataFrame({"a": [1, 2], "g": ["x", "y"]}))


@pytest.mark.parametrize("expr, expected", [
    ('df["a"].sum() * 100', 300),
    ('(df["a"] ** 2).sum()', 5),
    ('2**10', 1024),
])
def test_ordinary_arithmetic_still_evaluates(expr, expected):
    assert safe_eval_pandas(expr, pd.DataFrame({"a": [1, 2]})) == expected


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the capped child process is Linux-only")
def test_a_data_sized_blowup_is_stopped_and_the_caller_survives():
    import time

    from agents import dpc_verifier

    df = pd.DataFrame({"g": ["x"], "a": [4_000_000_000]})  # the factor comes from the data
    started = time.monotonic()
    with pytest.raises((MemoryError, TimeoutError)):
        dpc_verifier.safe_eval_pandas('df["g"] * df["a"].max()', df, timeout_s=10)
    assert time.monotonic() - started < 15
    # The parent process is intact and evaluates the next expression normally.
    assert dpc_verifier.safe_eval_pandas('df["a"].max()', df) == 4_000_000_000


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the capped child process is Linux-only")
def test_a_child_that_overruns_is_killed():
    from agents import dpc_verifier

    with pytest.raises(TimeoutError):
        dpc_verifier._run_in_capped_child(lambda: [0 for _ in iter(int, 1)], 0.5)
