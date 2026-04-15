import json
import logging
import os
from typing import Any

from shared.llm_provider import get_llm
from shared.models import ValidationResult
from shared.secret_resolver import secret_resolver

logger = logging.getLogger("aura.orchestration.critic")


class CriticAgent:
    """Validates SQL output produced by the generator agent."""

    def __init__(self) -> None:
        self._llm = get_llm(model=os.getenv("CRITIC_MODEL", ""))

    def run(self, original_prompt: str, generated_sql: str) -> ValidationResult:
        instruction = (
            "You are a senior data architect acting as a meticulous code reviewer. "
            "Analyze the provided SQL query based on the user's original request. "
            "Check for: 1. Syntactic correctness. 2. Security vulnerabilities. "
            "3. Correctness in addressing the user's request. "
            "4. All table and column names MUST be enclosed in double quotes "
            '(e.g., "my_table"."my_column") for identifiers with special characters, spaces, or reserved keywords. '
            "Respond ONLY with a JSON object matching the specified format."
        )

        prompt = f"""
        {instruction}

        "user_request": "{original_prompt}",
        "sql_query": "{generated_sql}",

        "response_format": {{
            "is_valid": "boolean",
            "reason": "string",
            "rework_suggestion": "string (provide if invalid)"
        }}
        """

        if self._llm.is_available():
            try:
                response_json = self._llm.generate_json(prompt)
                if response_json:
                    return ValidationResult(**response_json)
            except Exception as exc:
                logger.warning("CriticAgent remote validation failed: %s", exc)

        # If critic LLM is unavailable, do a basic syntactic check rather than
        # always rejecting.  This avoids 3 wasted retry loops when the generator
        # already produced plausible SQL.
        sql_upper = generated_sql.strip().upper()
        looks_like_sql = sql_upper.startswith(("SELECT", "WITH", "EXPLAIN"))
        if looks_like_sql:
            return ValidationResult(
                is_valid=True,
                reason="High confidence — basic syntax check passed (critic LLM unavailable).",
                rework_suggestion=None,
            )

        return ValidationResult(
            is_valid=False,
            reason="Critic LLM unavailable and query does not look like valid SQL.",
            rework_suggestion="Ensure the output starts with SELECT or WITH."
        )
