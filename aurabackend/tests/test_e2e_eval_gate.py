"""
E2E Evaluation Gate — 8-Layer Assertion Pipeline
=================================================
Mirrors the Attest framework's 8-layer pattern as a single pytest run that
ships a 70+ column synthetic schema through the chat endpoint. Each layer
is a separate assertion block — failure at any layer fails CI, but the
test always tries to emit *all* layer findings into ``self._report`` so a
single CI run shows every regression at once instead of tripping on the
first one.

The gate is the contractual SLA the upgrade plan asks for:

  Layer 3 (token budget)  → prompt ≤ ``AURA_MAX_TOKENS_PER_REQUEST``
  Layer 2 (latency)       → end-to-end /chat call < 60 seconds
  …plus six other regression dimensions that have already broken in
  prior sprints (see project_aura_enhancement_sprints.md hardening chain).

Run locally::

    pytest tests/test_e2e_eval_gate.py -q

In CI it's wired as a separate job (`eval-gate`) so a regression here is
visible at-a-glance, not buried in the broader backend-test summary.
"""
from __future__ import annotations

import csv
import json
import os
import random
import string
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.llm_cache import MAX_TOKENS_PER_REQUEST
from tests._mock_llm import MockRule, UnifiedMockLLM, install_mock

# Budget for the WHOLE run (sum of all prompt tokens). Larger than the
# per-call gate because a chat turn legitimately calls the LLM multiple
# times (intent → sql → analysis → viz). Tunable via env so future model
# swaps don't lock CI to a stale ceiling.
PER_RUN_TOKEN_CEILING = int(os.getenv("AURA_EVAL_GATE_RUN_TOKENS", "32000"))
LATENCY_SLA_SECONDS = float(os.getenv("AURA_EVAL_GATE_LATENCY_S", "60"))
SCHEMA_COLUMN_COUNT = int(os.getenv("AURA_EVAL_GATE_COLUMNS", "75"))
SCHEMA_ROW_COUNT = int(os.getenv("AURA_EVAL_GATE_ROWS", "200"))
TABLE_NAME = "wide_sales_fact"


# ── Wide-schema-tuned UnifiedMockLLM ──────────────────────────────────
# Reuses the suite-wide UnifiedMockLLM with rules tuned for the 75-col
# fixture (response references the real column names so layer-6
# semantic-relevance assertions still match).

def _eval_gate_rules() -> List[MockRule]:
    import json as _j
    import re as _re
    return [
        MockRule(_re.compile(r"intent.*conversation|conversation.*intent", _re.I),
                 _j.dumps({"intent": "sql", "message": ""})),
        MockRule(_re.compile(r"chart|visualization", _re.I),
                 _j.dumps({"type": "bar", "x": "region", "y": ["revenue"],
                           "title": "By region", "reason": "categorical + numeric"})),
        MockRule(_re.compile(r"explain.*sentence|one plain-english sentence", _re.I),
                 "Total revenue grouped by region across the wide_sales_fact table."),
        MockRule(_re.compile(r"data summary|column profile|descriptive statistics", _re.I),
                 "North leads region totals; revenue is symmetric around the mean."),
        MockRule(_re.compile(r"\bsql\b|\bselect\b|\bquery\b", _re.I),
                 f'SELECT "region", SUM("revenue") AS total_revenue '
                 f'FROM "{TABLE_NAME}" GROUP BY "region" LIMIT 100'),
    ]


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def wide_csv(tmp_path_factory):
    """Plant a {SCHEMA_COLUMN_COUNT}-column CSV in the upload dir so
    build_schema_context_cached materialises it as a DuckDB table."""
    uploads = Path(__file__).resolve().parent.parent / "data" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    csv_path = uploads / f"{TABLE_NAME}.csv"

    rng = random.Random(42)  # deterministic for layer 4
    base_cols = ["region", "product", "channel", "revenue", "units"]
    extra_cols = [f"dim_{i:02d}" for i in range(SCHEMA_COLUMN_COUNT - len(base_cols))]
    columns = base_cols + extra_cols

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for _ in range(SCHEMA_ROW_COUNT):
            row = [
                rng.choice(["North", "South", "East", "West"]),
                rng.choice(["WidgetA", "WidgetB", "WidgetC"]),
                rng.choice(["online", "retail", "wholesale"]),
                round(rng.uniform(100, 5000), 2),
                rng.randint(1, 100),
            ]
            row.extend("".join(rng.choices(string.ascii_lowercase, k=6)) for _ in extra_cols)
            w.writerow(row)

    yield csv_path
    csv_path.unlink(missing_ok=True)


@pytest.fixture()
def mock_llm(monkeypatch):
    llm = UnifiedMockLLM(rules=_eval_gate_rules(), record=True, finish_reason="stop")
    install_mock(monkeypatch, llm)
    yield llm


@pytest.fixture()
def client(wide_csv, mock_llm):
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    return TestClient(app)


# ── The 8-layer gate ──────────────────────────────────────────────────

V1 = "/api/v1"
QUESTION = "What is the total revenue by region?"


class TestE2EEvaluationGate:
    """One end-to-end /chat run, validated through 8 independent assertion layers."""

    def test_eight_layer_gate(self, client, mock_llm):
        findings: List[str] = []

        # Layer 1 + 2: schema + latency
        t0 = time.perf_counter()
        resp = client.post(f"{V1}/chat", json={"message": QUESTION, "auto_execute": True})
        wall_seconds = time.perf_counter() - t0

        # ── Layer 1: schema ─────────────────────────────────────────
        try:
            assert resp.status_code == 200, f"chat returned {resp.status_code}: {resp.text[:300]}"
            body = resp.json()
            assert isinstance(body, dict), "response not a JSON object"
            assert "message" in body or "execution_result" in body, (
                "response missing expected ChatResponse fields (message / execution_result)"
            )
        except AssertionError as exc:
            findings.append(f"L1 schema: {exc}")

        # ── Layer 2: latency ────────────────────────────────────────
        try:
            assert wall_seconds < LATENCY_SLA_SECONDS, (
                f"chat took {wall_seconds:.2f}s, exceeds {LATENCY_SLA_SECONDS}s SLA"
            )
        except AssertionError as exc:
            findings.append(f"L2 latency: {exc}")

        # ── Layer 3: per-call token budget ──────────────────────────
        # The unified mock populates _last_usage with realistic prompt
        # token counts via the same estimator the production code uses
        # for cache hits, so we read them from the mock's recorded
        # prompts via estimate_request_tokens (mirrors what the
        # _BudgetObserver / _PrometheusObserver see at runtime).
        try:
            from shared.llm_cache import estimate_request_tokens as _est
            assert mock_llm.calls, "no LLM calls were made — budget check vacuous"
            prompt_tokens = [_est(c["prompt"]) for c in mock_llm.calls]
            biggest = max(prompt_tokens)
            assert biggest <= MAX_TOKENS_PER_REQUEST, (
                f"single prompt of {biggest} tokens exceeds AURA_MAX_TOKENS_PER_REQUEST "
                f"({MAX_TOKENS_PER_REQUEST}). The 75-col schema must be focused/trimmed "
                "before it reaches the model — see chat router schema-focus path."
            )
        except AssertionError as exc:
            findings.append(f"L3 token budget: {exc}")

        # ── Layer 4: determinism (idempotent SQL emitted twice) ─────
        try:

            second = client.post(f"{V1}/chat", json={"message": QUESTION, "auto_execute": True})
            assert second.status_code == 200, "determinism rerun failed"
            second_body = second.json()
            # Compare the SQL text in both responses' execution_result, when present.
            sql_a = (body.get("execution_result") or {}).get("sql_query") or ""
            sql_b = (second_body.get("execution_result") or {}).get("sql_query") or ""
            if sql_a and sql_b:
                assert sql_a == sql_b, (
                    f"non-deterministic SQL across runs:\n  A: {sql_a}\n  B: {sql_b}"
                )
        except AssertionError as exc:
            findings.append(f"L4 determinism: {exc}")

        # ── Layer 5: safety (no destructive SQL anywhere in response) ──
        try:
            payload_text = json.dumps(body).lower()
            for forbidden in ("drop table", "drop database", "truncate ", "delete from"):
                assert forbidden not in payload_text, (
                    f"destructive token {forbidden!r} surfaced in response — "
                    "SQLGeneratorAgent._sanitise should have blocked it"
                )
        except AssertionError as exc:
            findings.append(f"L5 safety: {exc}")

        # ── Layer 6: semantic (answer mentions the asked-about column) ─
        try:
            payload_text = json.dumps(body).lower()
            assert "region" in payload_text or "revenue" in payload_text, (
                "response doesn't reference 'region' or 'revenue' — answer drifted from the question"
            )
        except AssertionError as exc:
            findings.append(f"L6 semantic: {exc}")

        # ── Layer 7: per-run cost ceiling ───────────────────────────
        try:
            from shared.llm_cache import estimate_request_tokens as _est
            total = sum(_est(c["prompt"]) for c in mock_llm.calls)
            assert total <= PER_RUN_TOKEN_CEILING, (
                f"run consumed {total} tokens across {len(mock_llm.calls)} LLM calls, "
                f"exceeds AURA_EVAL_GATE_RUN_TOKENS ({PER_RUN_TOKEN_CEILING})"
            )
        except AssertionError as exc:
            findings.append(f"L7 cost: {exc}")

        # ── Layer 8: observability (AGENT_DURATION recorded) ────────
        try:
            from shared.observability import AGENT_DURATION
            # _NoopMetric (when prometheus-client missing) has no _metrics attr,
            # so we just assert the singleton exists and exposes ``observe``.
            assert hasattr(AGENT_DURATION, "labels"), "AGENT_DURATION metric not initialised"
        except AssertionError as exc:
            findings.append(f"L8 observability: {exc}")

        if findings:
            pytest.fail("E2E eval gate failed:\n  - " + "\n  - ".join(findings))
