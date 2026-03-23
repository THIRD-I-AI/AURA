from __future__ import annotations

import os
import sys
from typing import Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from shared.llm_provider import get_llm
from shared.secret_resolver import secret_resolver


class GeneratorAgent:
    """Generates SQL using Gemini with deterministic fallbacks."""

    def __init__(self) -> None:
        self._llm = get_llm(model=os.getenv("GENERATOR_MODEL", ""))

    def run(self, prompt: str, context: str, rework_feedback: str = "") -> str:
        instruction = (
            "You are an expert data analyst. Your task is to convert a user's question "
            "into a syntactically correct SQL query. The database is DuckDB (compatible with PostgreSQL syntax). "
            "Use the provided database schema context — use ONLY the table and column names listed there. "
            "Respond ONLY with the raw SQL query, no markdown, no explanation."
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

        print(f"=== [SQL GENERATOR PROMPT] ===\n{chr(10).join(prompt_parts)}\n================================")

        if self._llm.is_available():
            try:
                raw = self._llm.generate(prompt_parts)
                print(f"=== [SQL GENERATOR RESPONSE] ===\n{raw}\n================================")
                if raw:
                    return raw.strip().replace("```sql", "").replace("```", "").strip()
            except Exception as exc:
                print(f"GeneratorAgent: remote generation failed - {exc}")

        return self.fallback(prompt, context)

    def fallback(self, prompt: str, context: str) -> str:

        # Extract table name and columns from context dynamically
        table_name = "data_table"  # default
        columns = []  # will be populated from context
        
        try:
            lines = context.split("\n")
            # Try to parse context in multiple formats
            if "Table:" in context and "Columns:" in context:
                # New format from build_schema_context():
                #   "Table: customer — 847 rows"
                #   "  Columns: customer_id (BIGINT), first_name (VARCHAR), ..."
                for i, line in enumerate(lines):
                    line = line.strip()
                    if line.startswith("Table:") and not columns:
                        # Parse "Table: customer — 847 rows" or "Table: customer - columns: ..."
                        tbl_part = line.split("Table:")[1].strip()
                        # Remove row count suffix like "— 847 rows"
                        for sep in ["\u2014", "--", "-"]:
                            if sep in tbl_part:
                                tbl_part = tbl_part.split(sep)[0].strip()
                                break
                        if tbl_part:
                            table_name = tbl_part.strip()
                        # Check if columns are on same line (old format)
                        if "columns:" in line.lower():
                            cols_str = line.lower().split("columns:")[1].strip()
                            columns = [c.strip() for c in cols_str.split(",") if c.strip()]
                        # Check next line for "Columns:" (new format)
                        elif i + 1 < len(lines):
                            next_line = lines[i + 1].strip()
                            if next_line.startswith("Columns:"):
                                cols_str = next_line.split("Columns:")[1].strip()
                                # Strip type annotations like "(BIGINT)"
                                import re
                                raw_cols = [c.strip() for c in cols_str.split(",") if c.strip()]
                                columns = [re.sub(r'\s*\([^)]*\)\s*$', '', c).strip() for c in raw_cols]
                        break  # use first table found
            elif "Table:" in context and "columns:" in context:
                # Legacy format: "Table: product — columns: id, name, price"
                for line in lines:
                    line = line.strip()
                    if line.startswith("Table:") and "columns:" in line:
                        parts = line.split("columns:")
                        tbl_part = parts[0].replace("Table:", "").replace("\u2014", "").replace("-", "").strip()
                        if tbl_part:
                            table_name = tbl_part
                        if len(parts) > 1:
                            cols_str = parts[1].strip()
                            columns = [c.strip() for c in cols_str.split(",") if c.strip()]
                        break
            elif "Schema:" in context:
                schema_part = context.split("Schema:")[1].strip()
                if "(" in schema_part and ")" in schema_part:
                    table_name = schema_part[:schema_part.index("(")].strip()
                    cols_str = schema_part[schema_part.index("(")+1:schema_part.index(")")].strip()
                    columns = [col.strip() for col in cols_str.split(",") if col.strip()]
            elif "File:" in context:
                lines = context.split("\n")
                for line in lines:
                    if "File:" in line:
                        filename = line.split("File:")[1].strip()
                        table_name = filename.replace(".csv", "").replace(".xlsx", "").replace(".json", "").replace(".parquet", "").replace(" ", "_")
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