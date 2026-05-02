"""
Data Agnostic Researcher (DAR) Specialist
==========================================
Single-purpose LLM agent for the headless research loop. Two modes:

* ``formulate`` — given a table's schema + recent distribution profile,
  emit 3-5 specific business questions an analyst would ask, each paired
  with the SQL that would answer it.

* ``score`` — given (question, SQL, result rows), classify the finding
  (anomaly | trend | correlation | summary), score importance 0..1,
  and produce a one-sentence summary an SRE/PM can act on.

The mode is dispatched via ``ctx.metadata["dar_mode"]``. Both modes
return JSON; the LangGraph node validates against Pydantic schemas
before letting the result land in state.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity

logger = logging.getLogger("aura.agents.dar_research")


_FORMULATE_PROMPT = """\
You are an autonomous data-research agent. Given the schema and a
distribution profile of one table, produce 3-5 specific business
questions a senior analyst would ask, and the SQL that answers each.

TABLE: "{table}"
SCHEMA + PROFILE:
{profile}

RULES:
1. Questions must be answerable from THIS table only (no joins to
   tables you can't see). Frame them in business terms, not analytics
   jargon.
2. Each SQL must be a single SELECT (no DDL/DML, no comments). Quote
   identifiers with double quotes. Always include a LIMIT clause; cap
   at 100 rows.
3. Prefer questions that surface anomalies or trends (extremes, time
   evolution, deviations) over summary statistics.
4. Return ONLY valid JSON, no markdown fences. Schema:
{{
  "questions": [
    {{"question": "...", "sql": "SELECT ... LIMIT 100"}},
    ...
  ]
}}
"""


_SCORE_PROMPT = """\
You are scoring one autonomous research finding for importance.

QUESTION: {question}
EXECUTED SQL:
{sql}

RESULT ({n_rows} rows):
{rows}

Classify the finding and score its importance for an analyst's morning
briefing. Be conservative — most queries return mundane results;
reserve high scores (>0.7) for genuinely surprising patterns.

Return ONLY valid JSON:
{{
  "finding_type": "anomaly | trend | correlation | summary",
  "summary": "one short sentence the user reads first",
  "score": 0.0,
  "is_anomaly": false
}}
"""


class DARResearchAgent(BaseAgent):
    name = "DARResearchAgent"
    description = "Headless research agent: formulates questions and scores findings."
    llm_model_env = "DAR_MODEL"

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        mode = ctx.metadata.get("dar_mode", "formulate")
        if mode == "formulate":
            return await self._formulate(ctx, result)
        if mode == "score":
            return await self._score(ctx, result)
        result.status = AgentStatus.FAILED
        result.error = f"unknown DAR mode: {mode!r}"
        return result

    async def _formulate(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        table = ctx.metadata.get("table_name", "")
        profile = ctx.metadata.get("profile_text", "")
        if not table or not profile:
            result.status = AgentStatus.FAILED
            result.error = "DAR formulate requires metadata.table_name + metadata.profile_text"
            return result

        prompt = _FORMULATE_PROMPT.format(table=table, profile=profile)
        await self._report("Formulating research questions…", 30)
        raw = await self._call_llm_json(prompt)
        if not raw or not isinstance(raw.get("questions"), list):
            result.status = AgentStatus.FAILED
            result.error = "DAR formulate: LLM returned no questions"
            result.add_step(action="formulate_empty", severity=Severity.WARNING)
            return result

        # Surface a sanitised list — the graph node validates each entry
        # against its Pydantic schema before persisting.
        questions: List[Dict[str, str]] = []
        for q in raw["questions"]:
            if not isinstance(q, dict):
                continue
            text = (q.get("question") or "").strip()
            sql = (q.get("sql") or "").strip()
            if text and sql and "select" in sql.lower():
                questions.append({"question": text, "sql": sql})

        result.output["questions"] = questions
        result.add_step(action="formulate_done",
                        output_summary=f"{len(questions)} question(s) formulated")
        return result

    async def _score(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        question = ctx.metadata.get("question", "")
        sql = ctx.metadata.get("sql", "")
        rows = ctx.metadata.get("rows") or []
        if not question:
            result.status = AgentStatus.FAILED
            result.error = "DAR score requires metadata.question"
            return result

        # Cap rows shown to the LLM — long result tables blow the prompt.
        sample_rows = rows[:20]
        prompt = _SCORE_PROMPT.format(
            question=question, sql=sql or "(SQL unavailable)",
            n_rows=len(rows), rows=json.dumps(sample_rows, default=str),
        )
        raw = await self._call_llm_json(prompt)
        if not raw:
            result.status = AgentStatus.FAILED
            result.error = "DAR score: LLM returned no scoring payload"
            return result

        finding_type = (raw.get("finding_type") or "summary").lower()
        if finding_type not in {"anomaly", "trend", "correlation", "summary"}:
            finding_type = "summary"

        try:
            score = float(raw.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))

        result.output["finding_type"] = finding_type
        result.output["summary"] = (raw.get("summary") or "").strip()
        result.output["score"] = score
        result.output["is_anomaly"] = bool(raw.get("is_anomaly", False))
        result.add_step(action="score_done",
                        output_summary=f"{finding_type} score={score:.2f}")
        return result

    async def _call_llm_json(self, prompt: str) -> Optional[Dict[str, Any]]:
        if not self.llm.is_available():
            return None
        try:
            return self.llm.generate_json(prompt)
        except Exception as exc:
            logger.warning("DAR LLM call failed: %s", exc)
            return None
