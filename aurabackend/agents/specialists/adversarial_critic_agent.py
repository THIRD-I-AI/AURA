"""
Adversarial Critic Agent
========================
Reads ``(estimates, refutations, dag, treatment, outcome)`` from
``ctx.upstream_results`` and emits a list of structured *checkable*
challenges to the proposed counterfactual conclusion. Output is strict
JSON — the engine validates each challenge against the
``AdversarialChallenge`` schema before letting it land in the artifact.

Severity rubric:

* ``high``   — an unobserved confounder, identifiability failure, or an
               estimator-vs-refutation contradiction that would invalidate
               the estimate if true.
* ``medium`` — a robustness concern that would materially widen the CI
               but probably not flip the sign.
* ``low``    — a stylistic / disclosure note (e.g. small ``n_samples``).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity

logger = logging.getLogger("aura.agents.adversarial_critic")


_PROMPT = """You are an adversarial peer reviewer for a counterfactual
analysis. You receive: a list of estimates from different methods,
refutation test outcomes, the DAG used, and the treatment+outcome.
Your job is to enumerate concrete, *checkable* objections to the
conclusion.

Respond ONLY with strict JSON, no markdown, no commentary:

{{ "challenges": [
    {{"text": "<one-sentence objection>",
     "severity": "low|medium|high",
     "suggested_check": "<actionable next step>"}},
    ...
] }}

Severity rubric:
* high   = an unobserved confounder, identifiability failure, or a
           direct contradiction between an estimator and a refutation
           that, if true, would invalidate the estimate.
* medium = a robustness concern that would materially widen the CI
           but probably not flip the sign.
* low    = a stylistic / disclosure note (e.g. small ``n_samples``).

Inputs:
* estimates: {estimates}
* refutations: {refutations}
* DAG edges: {dag}
* treatment: {treatment}
* outcome: {outcome}

Be concise. Return 2-5 challenges max. An empty list is valid if you
have no objections.
"""

_VALID_SEVERITY = {"low", "medium", "high"}


class AdversarialCriticAgent(BaseAgent):
    name = "AdversarialCriticAgent"
    description = "Generates structured challenges against a counterfactual estimate."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        upstream = ctx.upstream_results or {}
        prompt = _PROMPT.format(
            estimates=json.dumps(upstream.get("estimates", []), default=str),
            refutations=json.dumps(upstream.get("refutations", []), default=str),
            dag=json.dumps(upstream.get("dag", {}), default=str),
            treatment=json.dumps(upstream.get("treatment", {}), default=str),
            outcome=json.dumps(upstream.get("outcome", {}), default=str),
        )
        result.add_step(action="critique_prompt_built")

        try:
            raw = self.llm.generate(prompt) or "{}"
        except Exception as exc:
            result.error = f"LLM generate failed: {exc}"
            result.status = AgentStatus.FAILED
            result.add_step(action="critique_llm_error", output_summary=str(exc),
                            severity=Severity.ERROR)
            return result

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            result.error = f"Critic returned non-JSON: {exc}: {str(raw)[:200]}"
            result.status = AgentStatus.FAILED
            result.add_step(action="critique_non_json", output_summary=str(exc),
                            severity=Severity.WARNING)
            return result

        challenges: List[Dict[str, Any]] = []
        for c in data.get("challenges", []) or []:
            if not isinstance(c, dict) or "text" not in c:
                continue
            severity = c.get("severity", "low")
            if severity not in _VALID_SEVERITY:
                severity = "low"
            challenges.append({
                "text": str(c["text"])[:500],
                "severity": severity,
                "suggested_check": (
                    str(c["suggested_check"])[:500]
                    if c.get("suggested_check") is not None
                    else None
                ),
            })

        result.output = {"challenges": challenges}
        result.status = AgentStatus.SUCCESS
        result.add_step(action="critique_done",
                        output_summary=f"{len(challenges)} challenge(s)")
        return result
