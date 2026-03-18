import json
import os
import sys
from typing import Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from shared.llm_provider import get_llm
from shared.models import ValidationResult
from shared.secret_resolver import secret_resolver


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
                print(f"CriticAgent: remote validation failed - {exc}")

        return ValidationResult(
            is_valid=False,
            reason="Validation service unavailable, returning fallback guidance.",
            rework_suggestion="Review SQL manually for correctness and security."
        )