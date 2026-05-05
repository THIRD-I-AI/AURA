"""
Counterfactual Parser Agent
===========================
Parses an NL counterfactual question + table schema into a structured
``{treatment, outcome}`` payload that the engine can drop into a
``CounterfactualQuery``. Strict-JSON output only.

Reads from ``ctx.user_prompt`` (the question) and ``ctx.schema_context``
(table → column-list mapping).

If the user's question isn't a counterfactual at all, the agent returns
``status=FAILED`` with a structured ``error`` so the chat router can
fall back to the regular SQL path.
"""
from __future__ import annotations

import json
import logging

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity

logger = logging.getLogger("aura.agents.counterfactual_parser")


_PROMPT = """You parse natural-language counterfactual questions into
structured JSON. The user is asking some variant of: "what would X have
been if Y had been different?".

Pull out:
* treatment   = the variable Y, with actual + counterfactual numeric values
* outcome     = the variable X, with aggregation and time window

Available tables and columns:
{schema}

Question: {question}

Respond ONLY with strict JSON in this exact shape — no markdown, no commentary:

{{
  "treatment": {{"column": "<col>", "actual": <number>, "counterfactual": <number>}},
  "outcome":   {{"column": "<col>", "agg": "sum|mean|count",
                  "window": ["YYYY-MM-DD", "YYYY-MM-DD"]}}
}}

If the question is NOT a counterfactual (no "what if" / "would have"
framing), return:

{{"error": "<one-sentence reason>"}}
"""


class CounterfactualParserAgent(BaseAgent):
    name = "CounterfactualParserAgent"
    description = "Parses NL counterfactual questions into a structured query spec."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        prompt = _PROMPT.format(
            schema=json.dumps(ctx.schema_context or {}, default=str),
            question=ctx.user_prompt,
        )
        result.add_step(action="parser_prompt_built")

        try:
            raw = self.llm.generate(prompt) or "{}"
        except Exception as exc:
            result.error = f"LLM generate failed: {exc}"
            result.status = AgentStatus.FAILED
            result.add_step(action="parser_llm_error", output_summary=str(exc),
                            severity=Severity.ERROR)
            return result

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            result.error = f"Parser non-JSON: {exc}: {str(raw)[:200]}"
            result.status = AgentStatus.FAILED
            result.add_step(action="parser_non_json", output_summary=str(exc),
                            severity=Severity.WARNING)
            return result

        if "error" in data:
            result.error = f"Not a counterfactual: {data['error']}"
            result.output = {"error": data["error"]}
            result.status = AgentStatus.FAILED
            result.add_step(action="parser_not_counterfactual",
                            output_summary=str(data["error"]),
                            severity=Severity.WARNING)
            return result

        if not all(k in data for k in ("treatment", "outcome")):
            result.error = "Parser missing treatment or outcome keys"
            result.output = data
            result.status = AgentStatus.FAILED
            result.add_step(action="parser_missing_keys", severity=Severity.WARNING)
            return result

        # Light schema validation; the engine's Pydantic layer does the
        # full validation when the spec is wrapped in CounterfactualQuery.
        t = data["treatment"]
        o = data["outcome"]
        if not all(k in t for k in ("column", "actual", "counterfactual")):
            result.error = "treatment missing column/actual/counterfactual"
            result.status = AgentStatus.FAILED
            return result
        if not all(k in o for k in ("column", "agg", "window")):
            result.error = "outcome missing column/agg/window"
            result.status = AgentStatus.FAILED
            return result

        result.output = data
        result.status = AgentStatus.SUCCESS
        result.add_step(action="parser_done",
                        output_summary=f"{t['column']} → {o['column']}")
        return result
