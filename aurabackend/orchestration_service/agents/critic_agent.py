import json
import os
import sys
from typing import Any

import google.generativeai as genai

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from shared.models import ValidationResult
from shared.secret_resolver import secret_resolver


class CriticAgent:
    """Validates SQL output produced by the generator agent."""

    def __init__(self) -> None:
        self._api_key = secret_resolver.get_secret("GEMINI_API_KEY")
        self._model: Any = None
        if self._api_key:
            try:
                configure_fn = getattr(genai, "configure", None)
                if callable(configure_fn):
                    configure_fn(api_key=self._api_key)
                model_cls = getattr(genai, "GenerativeModel", None)
                generation_config_cls = getattr(genai, "GenerationConfig", None)
                generation_config = None
                if generation_config_cls:
                    generation_config = generation_config_cls(response_mime_type="application/json")
                if model_cls:
                    self._model = model_cls("gemini-pro", generation_config=generation_config)
            except Exception as exc:
                print(f"CriticAgent: failed to initialize Gemini model - {exc}")

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

        if self._model:
            try:
                response = self._model.generate_content(prompt)
                response_json = json.loads(response.text)
                return ValidationResult(**response_json)
            except Exception as exc:
                print(f"CriticAgent: remote validation failed - {exc}")

        return ValidationResult(
            is_valid=False,
            reason="Validation service unavailable, returning fallback guidance.",
            rework_suggestion="Review SQL manually for correctness and security."
        )