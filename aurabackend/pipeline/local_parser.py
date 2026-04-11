"""
Local NLP-to-Pipeline Parser  (LLM-free)
=========================================
Deterministic rule-based engine that converts natural language prompts
into Pipeline definitions using keyword extraction, pattern matching,
and schema-aware column resolution.

No external API calls. No token limits. No rate limits. Instant results.

Handles ~80% of common ETL requests:
  - filter / where / keep / remove rows
  - sort / order by
  - drop / remove columns
  - rename columns
  - add / compute / calculate columns
  - aggregate / group by / summarize
  - deduplicate / remove duplicates
  - cast / convert types
  - fill missing / impute / handle nulls
  - limit / top N rows
  - clean data (composite: fill nulls + cast types + dedup)
  - custom SQL passthrough

Falls back gracefully when the prompt is too complex for pattern matching.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pipeline.models import (
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    ProcessingStep,
    SinkType,
    SourceType,
    StepType,
)

logger = logging.getLogger("aura.pipeline.local_parser")


# ────────────────────────────────────────────────────────────────────
# Schema helpers
# ────────────────────────────────────────────────────────────────────

def _col_names(schema: Optional[Dict[str, Any]]) -> List[str]:
    """Extract column names from schema context."""
    if not schema:
        return []
    for info in schema.values():
        cols = info.get("columns", [])
        return [c["name"] for c in cols if isinstance(c, dict) and "name" in c]
    return []


def _col_types(schema: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Map column name → DuckDB type string from schema context."""
    if not schema:
        return {}
    for info in schema.values():
        cols = info.get("columns", [])
        return {
            c["name"]: c.get("type", "VARCHAR")
            for c in cols if isinstance(c, dict) and "name" in c
        }
    return {}


def _source_file(schema: Optional[Dict[str, Any]], available_files: Optional[List[str]]) -> Optional[str]:
    """Pick the source file from schema or file list."""
    if schema:
        for fname in schema:
            return fname
    if available_files:
        return available_files[0]
    return None


def _find_columns_in_text(text: str, all_columns: List[str]) -> List[str]:
    """Find column names mentioned in text (case-insensitive)."""
    text_lower = text.lower()
    found = []
    # Sort by length descending so longer names match first
    for col in sorted(all_columns, key=len, reverse=True):
        if col.lower() in text_lower:
            found.append(col)
    return found


# ────────────────────────────────────────────────────────────────────
# Pattern matchers — each returns (steps, confidence) or None
# ────────────────────────────────────────────────────────────────────

def _match_filter(prompt: str, columns: List[str], col_types: Dict[str, str]) -> List[ProcessingStep]:
    """Match filter/where/keep/remove row patterns."""
    steps: List[ProcessingStep] = []

    # Pattern: "filter/keep/where <column> <op> <value>"
    ops_map = {
        "greater than": ">", "more than": ">", "above": ">", "over": ">",
        "less than": "<", "below": "<", "under": "<",
        "equal to": "=", "equals": "=", "is": "=",
        "not equal": "!=", "not": "!=",
        ">=": ">=", "<=": "<=", ">": ">", "<": "<", "=": "=", "!=": "!=",
        "like": "LIKE", "contains": "LIKE",
    }

    prompt_lower = prompt.lower()

    # "where/filter/keep ... column op value"
    for col in columns:
        col_lower = col.lower()
        # Look for patterns like "column > 10", "column is not null", "column contains xyz"
        pattern = re.compile(
            rf'\b{re.escape(col_lower)}\b\s*'
            rf'(is\s+not\s+null|is\s+null|>=|<=|!=|>|<|=|'
            rf'greater\s+than|less\s+than|more\s+than|above|below|over|under|'
            rf'equal\s+to|equals|not\s+equal|contains|like)\s*'
            rf'["\']?([^,\.\n"\']*?)["\']?\s*(?:[,\.]|and\b|$)',
            re.IGNORECASE,
        )
        for m in pattern.finditer(prompt):
            op_text = m.group(1).strip().lower()
            value = m.group(2).strip() if m.group(2) else ""

            if op_text in ("is not null",):
                operator, value = "IS NOT NULL", ""
            elif op_text in ("is null",):
                operator, value = "IS NULL", ""
            elif op_text in ("contains", "like"):
                operator = "LIKE"
                value = f"%{value}%"
            else:
                operator = ops_map.get(op_text, op_text.upper())

            steps.append(ProcessingStep(
                type=StepType.FILTER,
                description=f"Filter {col} {operator} {value}".strip(),
                config={"column": col, "operator": operator, "value": value},
            ))

    # "remove/exclude rows where ..."
    if re.search(r'\b(remove|exclude|delete)\b.*\brows?\b', prompt_lower) and not steps:
        # Try to find a null-check pattern
        for col in columns:
            if col.lower() in prompt_lower and ("null" in prompt_lower or "missing" in prompt_lower or "empty" in prompt_lower):
                steps.append(ProcessingStep(
                    type=StepType.FILTER,
                    description=f"Remove rows where {col} is null",
                    config={"column": col, "operator": "IS NOT NULL", "value": ""},
                ))

    # "keep only rows where statefips is not null" (generic)
    if re.search(r'\bkeep\b.*\bnot\s+null\b', prompt_lower):
        mentioned = _find_columns_in_text(prompt, columns)
        for col in mentioned:
            if not any(s.config.get("column") == col for s in steps):
                steps.append(ProcessingStep(
                    type=StepType.FILTER,
                    description=f"Keep rows where {col} is not null",
                    config={"column": col, "operator": "IS NOT NULL", "value": ""},
                ))

    return steps


def _match_sort(prompt: str, columns: List[str]) -> List[ProcessingStep]:
    """Match sort/order patterns."""
    steps: List[ProcessingStep] = []
    prompt_lower = prompt.lower()

    if not re.search(r'\b(sort|order)\b', prompt_lower):
        return steps

    mentioned = _find_columns_in_text(prompt, columns)
    for col in mentioned:
        direction = "DESC" if re.search(r'\b(desc|descending|high.to.low|largest|biggest)\b', prompt_lower) else "ASC"
        steps.append(ProcessingStep(
            type=StepType.SORT,
            description=f"Sort by {col} {direction}",
            config={"column": col, "direction": direction},
        ))

    return steps


def _match_drop_columns(prompt: str, columns: List[str]) -> List[ProcessingStep]:
    """Match drop/remove column patterns."""
    prompt_lower = prompt.lower()

    if not re.search(r'\b(drop|remove|delete|exclude)\b.*\bcolumn', prompt_lower):
        return []

    mentioned = _find_columns_in_text(prompt, columns)
    if mentioned:
        return [ProcessingStep(
            type=StepType.DROP_COLUMNS,
            description=f"Drop columns: {', '.join(mentioned)}",
            config={"columns": mentioned},
        )]
    return []


def _match_rename(prompt: str, columns: List[str]) -> List[ProcessingStep]:
    """Match rename column patterns."""
    prompt_lower = prompt.lower()
    if not re.search(r'\brename\b', prompt_lower):
        return []

    # Pattern: "rename <old> to <new>"
    mapping = {}
    for col in columns:
        pattern = re.compile(
            rf'\brename\b.*\b{re.escape(col.lower())}\b\s*(?:to|as|→)\s*(\w+)',
            re.IGNORECASE,
        )
        m = pattern.search(prompt)
        if m:
            mapping[col] = m.group(1)

    if mapping:
        return [ProcessingStep(
            type=StepType.RENAME_COLUMNS,
            description=f"Rename: {', '.join(f'{k}→{v}' for k, v in mapping.items())}",
            config={"mapping": mapping},
        )]
    return []


def _match_aggregate(prompt: str, columns: List[str]) -> List[ProcessingStep]:
    """Match aggregate/group by/summarize patterns."""
    prompt_lower = prompt.lower()

    if not re.search(r'\b(aggregate|group\s*by|summarize|summarise|sum\b|average|avg|count|min\b|max\b)', prompt_lower):
        return []

    mentioned = _find_columns_in_text(prompt, columns)

    # Detect aggregation function
    agg_func = "COUNT"
    if re.search(r'\b(average|avg|mean)\b', prompt_lower):
        agg_func = "AVG"
    elif re.search(r'\bsum\b', prompt_lower):
        agg_func = "SUM"
    elif re.search(r'\bmin\b', prompt_lower):
        agg_func = "MIN"
    elif re.search(r'\bmax\b', prompt_lower):
        agg_func = "MAX"

    # "group by" columns
    group_match = re.search(r'group\s*by\s+([\w\s,]+?)(?:\s+and\s+|\s*$|\s*,\s*(?:then|and)\s)', prompt_lower)
    group_cols = []
    if group_match:
        group_text = group_match.group(1)
        group_cols = [c for c in columns if c.lower() in group_text.lower()]

    if not group_cols and mentioned:
        # First mentioned column is likely the group-by, others are aggregation targets
        group_cols = mentioned[:1]
        agg_cols = mentioned[1:]
    else:
        agg_cols = [c for c in mentioned if c not in group_cols]

    if not agg_cols:
        agg_cols = ["*"]

    aggregations = [
        {"function": agg_func, "column": col, "alias": f"{agg_func.lower()}_{col}"}
        for col in agg_cols
    ]

    return [ProcessingStep(
        type=StepType.AGGREGATE,
        description=f"Aggregate: {agg_func} by {', '.join(group_cols)}",
        config={"group_by": group_cols, "aggregations": aggregations},
    )]


def _match_deduplicate(prompt: str, columns: List[str]) -> List[ProcessingStep]:
    """Match dedup/duplicate/unique patterns."""
    prompt_lower = prompt.lower()
    if not re.search(r'\b(dedup|deduplicate|duplicate|unique|distinct)\b', prompt_lower):
        return []

    mentioned = _find_columns_in_text(prompt, columns)
    return [ProcessingStep(
        type=StepType.DEDUPLICATE,
        description="Remove duplicates" + (f" on {', '.join(mentioned)}" if mentioned else ""),
        config={"columns": mentioned},
    )]


def _match_cast_type(prompt: str, columns: List[str], col_types: Dict[str, str]) -> List[ProcessingStep]:
    """Match cast/convert type patterns."""
    steps: List[ProcessingStep] = []
    prompt_lower = prompt.lower()

    if not re.search(r'\b(cast|convert|change\s+type|as\s+integer|as\s+int|to\s+integer|to\s+int|fix\s+type)', prompt_lower):
        return steps

    type_map = {
        "integer": "INTEGER", "int": "INTEGER", "number": "INTEGER",
        "float": "DOUBLE", "double": "DOUBLE", "decimal": "DOUBLE", "numeric": "DOUBLE",
        "string": "VARCHAR", "text": "VARCHAR", "varchar": "VARCHAR",
        "boolean": "BOOLEAN", "bool": "BOOLEAN",
        "date": "DATE", "timestamp": "TIMESTAMP",
    }

    mentioned = _find_columns_in_text(prompt, columns)
    for col in mentioned:
        # Try to find "cast col to/as TYPE"
        pattern = re.compile(
            rf'\b{re.escape(col.lower())}\b\s*(?:to|as)\s+(\w+)',
            re.IGNORECASE,
        )
        m = pattern.search(prompt)
        if m:
            target = type_map.get(m.group(1).lower(), m.group(1).upper())
            steps.append(ProcessingStep(
                type=StepType.CAST_TYPE,
                description=f"Cast {col} to {target}",
                config={"column": col, "new_type": target},
            ))

    return steps


def _match_fill_missing(prompt: str, columns: List[str], col_types: Dict[str, str]) -> List[ProcessingStep]:
    """Match fill missing / handle null patterns."""
    steps: List[ProcessingStep] = []
    prompt_lower = prompt.lower()

    if not re.search(r'\b(fill|impute|handle|replace)\b.*\b(missing|null|empty|nan|na)\b', prompt_lower):
        return steps

    # Detect strategy
    strategy = "mean"
    if re.search(r'\b(median)\b', prompt_lower):
        strategy = "median"
    elif re.search(r'\b(zero|0)\b', prompt_lower):
        strategy = "value"
    elif re.search(r'\b(mode|most\s+common)\b', prompt_lower):
        strategy = "value"

    mentioned = _find_columns_in_text(prompt, columns)

    if mentioned:
        for col in mentioned:
            fill_value = None
            if strategy == "value" and re.search(r'\b(zero|0)\b', prompt_lower):
                fill_value = "0"
            steps.append(ProcessingStep(
                type=StepType.FILL_MISSING,
                description=f"Fill missing {col} with {strategy}",
                config={"column": col, "strategy": strategy, "fill_value": fill_value},
            ))
    else:
        # Fill all numeric columns
        for col_name, col_type in col_types.items():
            type_upper = col_type.upper()
            if any(t in type_upper for t in ("INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "BIGINT", "REAL")):
                steps.append(ProcessingStep(
                    type=StepType.FILL_MISSING,
                    description=f"Fill missing {col_name} with {strategy}",
                    config={"column": col_name, "strategy": strategy, "fill_value": None},
                ))

    return steps


def _match_limit(prompt: str) -> List[ProcessingStep]:
    """Match limit/top N patterns."""
    m = re.search(r'\b(?:limit|top|first|head)\s+(\d+)', prompt, re.IGNORECASE)
    if m:
        count = int(m.group(1))
        return [ProcessingStep(
            type=StepType.LIMIT,
            description=f"Limit to {count} rows",
            config={"count": count},
        )]
    return []


def _match_add_column(prompt: str, columns: List[str]) -> List[ProcessingStep]:
    """Match add/compute/calculate column patterns."""
    steps: List[ProcessingStep] = []
    prompt_lower = prompt.lower()

    if not re.search(r'\b(add|compute|calculate|create)\b.*\bcolumn\b', prompt_lower):
        return steps

    # Pattern: "add column <name> as/= <expression>"
    pattern = re.compile(
        r'\b(?:add|create|compute)\s+(?:a\s+)?column\s+(\w+)\s+(?:as|=|that\s+is)\s+(.+?)(?:\.|,|$)',
        re.IGNORECASE,
    )
    for m in pattern.finditer(prompt):
        name = m.group(1)
        expression = m.group(2).strip()
        steps.append(ProcessingStep(
            type=StepType.ADD_COLUMN,
            description=f"Add column {name} = {expression}",
            config={"name": name, "expression": expression},
        ))

    return steps


# ────────────────────────────────────────────────────────────────────
# Composite patterns (e.g., "clean the data")
# ────────────────────────────────────────────────────────────────────

def _match_clean_data(prompt: str, columns: List[str], col_types: Dict[str, str]) -> List[ProcessingStep]:
    """
    Match generic "clean" / "prepare" / "tidy" requests.
    Generates sensible defaults: dedup, fill numeric nulls, remove null key columns.
    """
    prompt_lower = prompt.lower()
    if not re.search(r'\b(clean|prepare|tidy|preprocess|sanitize)\b.*\bdata\b', prompt_lower):
        return []

    steps: List[ProcessingStep] = []

    # 1. Deduplicate
    steps.append(ProcessingStep(
        type=StepType.DEDUPLICATE,
        description="Remove duplicate rows",
        config={"columns": []},
    ))

    # 2. Fill missing numeric columns with mean
    numeric_cols = []
    for col_name, col_type in col_types.items():
        type_upper = col_type.upper()
        if any(t in type_upper for t in ("INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "BIGINT", "REAL")):
            numeric_cols.append(col_name)

    for col_name in numeric_cols[:10]:  # cap at 10 to keep pipeline manageable
        steps.append(ProcessingStep(
            type=StepType.FILL_MISSING,
            description=f"Fill missing {col_name} with mean",
            config={"column": col_name, "strategy": "mean", "fill_value": None},
        ))

    return steps


# ────────────────────────────────────────────────────────────────────
# Sink detection
# ────────────────────────────────────────────────────────────────────

def _detect_sink(prompt: str) -> PipelineSink:
    """Determine output format from the prompt."""
    prompt_lower = prompt.lower()

    if re.search(r'\b(export|save|write|output)\b.*\bparquet\b', prompt_lower):
        return PipelineSink(type=SinkType.FILE, format="parquet")
    if re.search(r'\b(export|save|write|output)\b.*\bjson\b', prompt_lower):
        return PipelineSink(type=SinkType.FILE, format="json")
    if re.search(r'\b(export|save|write|output)\b.*\bcsv\b', prompt_lower):
        return PipelineSink(type=SinkType.FILE, format="csv")
    if re.search(r'\b(export|save|write|download)\b', prompt_lower):
        return PipelineSink(type=SinkType.FILE, format="csv")

    return PipelineSink(type=SinkType.PREVIEW)


# ────────────────────────────────────────────────────────────────────
# Pipeline name generation
# ────────────────────────────────────────────────────────────────────

def _generate_name(prompt: str, source_file: Optional[str]) -> str:
    """Create a short pipeline name from the prompt."""
    base = Path(source_file).stem.replace("_", " ").title() if source_file else "Data"
    # Take first ~4 meaningful words from prompt
    words = re.findall(r'[a-zA-Z]+', prompt)
    action_words = [w for w in words[:6] if len(w) > 2 and w.lower() not in (
        "the", "and", "for", "with", "from", "all", "data", "file", "please", "can", "you",
    )]
    if action_words:
        return f"{' '.join(w.title() for w in action_words[:3])} — {base}"
    return f"Pipeline — {base}"


# ────────────────────────────────────────────────────────────────────
# Main parse entry point
# ────────────────────────────────────────────────────────────────────

class LocalPipelineParser:
    """
    Deterministic NLP-to-Pipeline parser.  No LLM required.

    Usage:
        parser = LocalPipelineParser()
        result = parser.parse(prompt, schema_context, available_files)
        if result is not None:
            pipeline, confidence = result
    """

    # Minimum confidence to accept the result (0.0 – 1.0)
    MIN_CONFIDENCE = 0.3

    def parse(
        self,
        prompt: str,
        schema_context: Optional[Dict[str, Any]] = None,
        available_files: Optional[List[str]] = None,
        source_file: Optional[str] = None,
    ) -> Optional[Tuple[Pipeline, float]]:
        """
        Try to parse a natural language prompt into a Pipeline.

        Returns:
            (Pipeline, confidence) if successful, where confidence is 0.0–1.0.
            None if the prompt is too complex for pattern matching.
        """
        columns = _col_names(schema_context)
        ctypes = _col_types(schema_context)
        file_name = source_file or _source_file(schema_context, available_files)

        all_steps: List[ProcessingStep] = []
        matched_patterns = 0

        # Run all matchers
        matchers = [
            ("clean",       lambda: _match_clean_data(prompt, columns, ctypes)),
            ("filter",      lambda: _match_filter(prompt, columns, ctypes)),
            ("sort",        lambda: _match_sort(prompt, columns)),
            ("drop",        lambda: _match_drop_columns(prompt, columns)),
            ("rename",      lambda: _match_rename(prompt, columns)),
            ("aggregate",   lambda: _match_aggregate(prompt, columns)),
            ("dedup",       lambda: _match_deduplicate(prompt, columns)),
            ("cast",        lambda: _match_cast_type(prompt, columns, ctypes)),
            ("fill",        lambda: _match_fill_missing(prompt, columns, ctypes)),
            ("limit",       lambda: _match_limit(prompt)),
            ("add_column",  lambda: _match_add_column(prompt, columns)),
        ]

        for name, matcher in matchers:
            try:
                steps = matcher()
                if steps:
                    all_steps.extend(steps)
                    matched_patterns += 1
                    logger.debug("[LocalParser] Matched '%s': %d steps", name, len(steps))
            except Exception as e:
                logger.warning("[LocalParser] Matcher '%s' failed: %s", name, e)

        if not all_steps:
            logger.info("[LocalParser] No patterns matched for prompt: %s", prompt[:100])
            return None

        # Calculate confidence based on how many words we could map to operations
        prompt_words = len(re.findall(r'\w+', prompt))
        # More patterns matched = higher confidence; short prompts = higher confidence
        confidence = min(1.0, (matched_patterns * 0.25) + (len(all_steps) * 0.1))
        # Bonus for matching columns
        mentioned_cols = len(_find_columns_in_text(prompt, columns))
        if mentioned_cols > 0:
            confidence = min(1.0, confidence + 0.15)
        # Penalty for very long prompts (likely complex)
        if prompt_words > 50:
            confidence *= 0.7

        if confidence < self.MIN_CONFIDENCE:
            logger.info(
                "[LocalParser] Confidence too low (%.2f) for: %s",
                confidence, prompt[:100],
            )
            return None

        # Build Pipeline
        source = PipelineSource(
            type=SourceType.FILE,
            file_name=file_name,
        )
        sink = _detect_sink(prompt)

        pipeline = Pipeline(
            name=_generate_name(prompt, file_name),
            description=f"Auto-generated from: {prompt[:200]}",
            source=source,
            steps=all_steps,
            sink=sink,
            status=PipelineStatus.READY,
            generated_from_prompt=prompt,
            tags=["local-parser", "llm-free"],
        )

        logger.info(
            "[LocalParser] Generated pipeline '%s' with %d steps (confidence=%.2f)",
            pipeline.name, len(pipeline.steps), confidence,
        )
        return pipeline, confidence
