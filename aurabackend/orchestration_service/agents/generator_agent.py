from __future__ import annotations

import os
import sys
from typing import Any

import google.generativeai as genai

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from shared.secret_resolver import secret_resolver


class GeneratorAgent:
    """Generates SQL using Gemini with deterministic fallbacks."""

    def __init__(self) -> None:
        self._model_name = os.getenv("GENERATOR_MODEL", "gemini-pro")
        self._api_key = secret_resolver.get_secret("GEMINI_API_KEY")
        self._model: Any = None
        if self._api_key:
            try:
                configure_fn = getattr(genai, "configure", None)
                if callable(configure_fn):
                    configure_fn(api_key=self._api_key)
                model_cls = getattr(genai, "GenerativeModel", None)
                if model_cls:
                    self._model = model_cls(self._model_name)
            except Exception as exc:
                print(f"GeneratorAgent: failed to initialize Gemini model - {exc}")

    def run(self, prompt: str, context: str, rework_feedback: str = "") -> str:
        instruction = (
            "You are an expert data analyst. Your task is to convert a user's question "
            "into a syntactically correct SQL query for a PostgreSQL database. "
            "Use the provided database schema context. Respond ONLY with the SQL query."
        )

        prompt_parts = [
            instruction,
            f"Database Schema Context:\n{context}",
            f"User's question:\n{prompt}",
        ]

        if rework_feedback:
            prompt_parts.append(
                "This is a rework attempt. The previous query was flawed. "
                f"Please correct it based on this feedback:\n{rework_feedback}"
            )

        if self._model:
            try:
                response = self._model.generate_content(prompt_parts)
                return response.text.strip().replace("```sql", "").replace("```", "").strip()
            except Exception as exc:
                print(f"GeneratorAgent: remote generation failed - {exc}")

        return self.fallback(prompt, context)

    def fallback(self, prompt: str, context: str) -> str:


        # Extract table name and columns from context dynamically
        table_name = "sales_table"  # default
        columns = ["product_name", "total_revenue", "sale_date"]  # default
        
        try:
            # Try to parse context to extract schema
            # Format: "Schema: table_name(col1, col2, col3)"
            if "Schema:" in context:
                schema_part = context.split("Schema:")[1].strip()
                if "(" in schema_part and ")" in schema_part:
                    table_name = schema_part[:schema_part.index("(")].strip()
                    cols_str = schema_part[schema_part.index("(")+1:schema_part.index(")")].strip()
                    columns = [col.strip() for col in cols_str.split(",") if col.strip()]
            # Also try "File: " format from dynamic uploads
            elif "File:" in context:
                # Extract filename and schema from upload context
                lines = context.split("\n")
                for line in lines:
                    if "File:" in line:
                        filename = line.split("File:")[1].strip()
                        table_name = filename.replace(".csv", "").replace(".xlsx", "").replace(".json", "").replace(".parquet", "").replace("[^a-zA-Z0-9_]", "_")
                    elif "Schema:" in line:
                        schema_part = line.split("Schema:")[1].strip()
                        if "(" in schema_part and ")" in schema_part:
                            cols_str = schema_part[schema_part.index("(")+1:schema_part.index(")")].strip()
                            columns = [col.strip() for col in cols_str.split(",") if col.strip()]
        except Exception:
            pass  # Fall back to defaults if parsing fails
        
        # Generate fallback query using actual schema
        prompt_lower = prompt.lower()

        if not columns or len(columns) == 0:
            columns = ["column_0", "column_1", "column_2"]  # Safe default for headerless CSV

        # Build a useful summary: row count + distinct counts per column, plus a top-frequency sample on the first column
        summary_select_parts = ["COUNT(*) AS row_count"]
        for col in columns:
            summary_select_parts.append(f"COUNT(DISTINCT {col}) AS distinct_{col}")
        summary_query = f"SELECT {', '.join(summary_select_parts)} FROM {table_name}"

        # Top frequency for the first column (helps when asking for sales data or categories)
        top_freq_query = (
            f"SELECT {columns[0]}, COUNT(*) AS count FROM {table_name} "
            f"GROUP BY {columns[0]} ORDER BY count DESC LIMIT 5"
        )

        # Quick preview of first rows with up to five columns
        preview_cols = ", ".join(columns[:5])
        preview_query = f"SELECT {preview_cols} FROM {table_name} LIMIT 5"

        # If the user explicitly asks for a summary/report, return the richer bundle
        if "summary" in prompt_lower or "report" in prompt_lower or "sales" in prompt_lower:
            return f"{summary_query}; {top_freq_query}; {preview_query};"

        if "top" in prompt_lower:
            return f"{top_freq_query};"
        if "count" in prompt_lower or "how many" in prompt_lower:
            return summary_query + ";"
        if "distribution" in prompt_lower or "unique" in prompt_lower:
            return f"{top_freq_query};"

        # Generic fallback: preview rows
        return f"{preview_query};"