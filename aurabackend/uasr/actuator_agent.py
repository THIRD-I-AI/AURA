"""
UASR Synthesis Actuator Agent
================================
Generates JIT (Just-In-Time) "shim" scripts to bridge data drift.

Given a DiagnosisResult, the Actuator:
  1. Selects a shim template based on the diagnosis
  2. Uses LLM (with rule-based fallback) to generate a Python transformation script
  3. Returns the shim code for sandbox validation
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sys
import textwrap
from collections import Counter
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, cast

import numpy as np

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity
from agents.params import ActuatorAgentParams
from uasr.drift_detector import DriftDetector
from uasr.models import ColumnDistribution, DiagnosisResult, DriftType, ShimResult

logger = logging.getLogger("uasr.actuator")


class SynthesisActuatorAgent(BaseAgent):
    """
    Generates Python-based data transformation shims to repair drift.

    Two-tier generation:
      1. Template-based (instant, no LLM) for common patterns
      2. LLM-assisted for complex transformations
    """

    name = "SynthesisActuatorAgent"
    description = "Generates JIT shim scripts to bridge detected data drift."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Generating recovery shim…", 10)

        params = cast(ActuatorAgentParams, ctx.metadata or {})
        diagnosis_data = params.get("diagnosis")

        recovery_id = params.get("recovery_id", ctx.run_id)

        if isinstance(diagnosis_data, dict):
            diagnosis = DiagnosisResult(**diagnosis_data)
        elif isinstance(diagnosis_data, DiagnosisResult):
            diagnosis = diagnosis_data
        else:
            result.status = AgentStatus.FAILED
            result.error = "No diagnosis provided in metadata"
            return result

        drift_type = params.get("drift_type", "unknown")
        drift_vector = params.get("drift_vector", {})

        await self._report(f"Drift type: {drift_type}, generating shim…", 30)

        # Try template-based generation first. Record which generator won —
        # S41's risk gate auto-deploys only deterministic "template" shims.
        generation_method = "template"
        shim_code = self._template_shim(drift_type, drift_vector, diagnosis)

        # A severe LOCATION SHIFT dispatches to the outlier-clip template
        # above, but clipping is the wrong repair for it: the whole batch's
        # centroid moved, not just a handful of extreme values, so clipping
        # to baseline_mean +/- 3*std collapses every row onto the clip
        # boundary -- a degenerate spike that makes divergence WORSE, not
        # better (measured live: N(100,10) -> N(250,10) clipped to a spike
        # at 130, pre_kl=44.6108 -> post_kl=122.6068). Auto-rescaling
        # production values to satisfy a divergence metric would hide
        # real-world change from downstream consumers -- detecting and
        # escalating is honest, silently rewriting data is not. Escalate to
        # the S41 human-approval queue instead of deploying the clip shim.
        # Rescale (unit-bug) fixes and mild pass-through monitors already
        # dispatch to a different template and are untouched.
        escalation_reason = None
        if shim_code and "UASR Shim - Outlier Clipping" in shim_code:
            escalation_reason = self._location_shift_reason(drift_vector)
            if escalation_reason:
                generation_method = "escalation"
                shim_code = self._escalation_shim(diagnosis, escalation_reason)
        if shim_code and not escalation_reason:
            # ADR-002 phase 2a: none of these templates is proven on the data. Dropping a
            # new column, casting a failed value to None, defaulting a removed column to
            # None, renaming by list order, clipping, and rescaling by a mean ratio are all
            # guesses. They stay as proposals in the approval queue; the only automatic
            # repairs are the per-batch proven rules (prove_* below, RecoveryLoop.prove_repairs).
            escalation_reason = (
                f"ADR-002: the {_template_name(shim_code)} template is not a proven repair; "
                "it is proposed for review and never applied automatically"
            )

        if not shim_code:
            await self._report("Using LLM for shim generation…", 50)
            generation_method = "llm"
            shim_code = await self._llm_shim(drift_type, drift_vector, diagnosis)

        if not shim_code:
            generation_method = "fallback"
            shim_code = self._fallback_shim(drift_type, diagnosis)

        await self._report("Shim generated successfully", 90)

        shim_result = ShimResult(
            recovery_id=recovery_id,
            shim_code=shim_code,
            language="python",
            generation_method=generation_method,
            requires_human_review=bool(escalation_reason),
            escalation_reason=escalation_reason,
        )

        result.output = {
            "shim": shim_result.model_dump(),
            "drift_type": drift_type,
            "diagnosis_summary": diagnosis.root_cause,
        }
        result.artifacts["shim"] = shim_result
        result.artifacts["shim_code"] = shim_code

        result.add_step(
            action="generate_shim",
            tool_name="template_engine",
            input_summary=f"drift_type={drift_type}, action={diagnosis.suggested_action[:100]}",
            output_summary=f"Generated {len(shim_code)} char Python shim",
            severity=Severity.INFO,
        )

        return result

    # ────────────────────────────────────────────────────────────────
    # Template-based shim generation
    # ────────────────────────────────────────────────────────────────

    def _template_shim(
        self,
        drift_type: str,
        drift_vector: Dict[str, Any],
        diagnosis: DiagnosisResult,
    ) -> Optional[str]:
        """Generate a shim from pre-built templates for common drift patterns."""

        if drift_type == DriftType.SCHEMA.value or drift_type == "schema":
            return self._schema_shim(drift_vector, diagnosis)
        elif drift_type == DriftType.STATISTICAL.value or drift_type == "statistical":
            return self._statistical_shim(drift_vector, diagnosis)
        elif drift_type == DriftType.SEMANTIC.value or drift_type == "semantic":
            return None  # Semantic shims need LLM
        return None

    def _schema_shim(self, drift_vector: Dict, diagnosis: DiagnosisResult) -> Optional[str]:
        # BUG-073: every column name spliced into generated shim source below
        # uses `{col!r}` (Python's own repr, which correctly escapes quotes/
        # backslashes) rather than manual f'"{col}"' wrapping. Column names
        # come from drift_vector, itself derived from an upstream (possibly
        # external/influenced) source schema -- an unescaped quote character
        # in a column name used to let arbitrary Python statements break out
        # of the generated string literal before the shim ever reached
        # sandbox validation. Same class of bug shared/sql_identifiers.py's
        # quote_identifier exists to prevent for SQL splicing (security.md).
        change_type = drift_vector.get("type", "schema_change")

        if change_type == "type_change":
            old_types = drift_vector.get("old_types", {})
            new_types = drift_vector.get("new_types", {})

            cast_lines = []
            for col in old_types:
                old_t = old_types[col]
                new_t = new_types.get(col, old_t)
                if old_t != new_t:
                    cast_lines.append(
                        f'        if {col!r} in row:\n'
                        f'            try:\n'
                        f'                row[{col!r}] = {_python_cast(old_t)}(row[{col!r}])\n'
                        f'            except (ValueError, TypeError):\n'
                        f'                row[{col!r}] = None  # Could not cast, set to None'
                    )

            if not cast_lines:
                return None

            body = "\n".join(cast_lines)
            return (
                '"""UASR Shim - Type Cast Recovery\n'
                'Converts changed column types back to expected types.\n'
                f'Generated for drift: {_doc_safe(diagnosis.root_cause)}\n'
                '"""\n\n'
                'def transform(rows: list[dict]) -> list[dict]:\n'
                '    """Apply type-cast recovery to each row."""\n'
                '    result = []\n'
                '    for row in rows:\n'
                f'{body}\n'
                '        result.append(row)\n'
                '    return result\n'
            )

        added = drift_vector.get("added", [])
        removed = drift_vector.get("removed", [])

        if removed and not added:
            default_lines = "\n".join(
                f'        row.setdefault({col!r}, None)'
                for col in removed
            )
            return (
                '"""UASR Shim - Missing Column Recovery\n'
                'Adds default values for columns removed from upstream.\n'
                f'Generated for drift: {_doc_safe(diagnosis.root_cause)}\n'
                '"""\n\n'
                'def transform(rows: list[dict]) -> list[dict]:\n'
                '    """Restore missing columns with NULL defaults."""\n'
                '    result = []\n'
                '    for row in rows:\n'
                f'{default_lines}\n'
                '        result.append(row)\n'
                '    return result\n'
            )

        if added and not removed:
            keep_cols_str = ", ".join(f'{c!r}' for c in added)
            return (
                '"""UASR Shim - Column Filter\n'
                'Strips unexpected new columns to maintain schema compatibility.\n'
                f'Generated for drift: {_doc_safe(diagnosis.root_cause)}\n'
                '"""\n\n'
                f'_NEW_COLUMNS = [{keep_cols_str}]\n\n'
                'def transform(rows: list[dict]) -> list[dict]:\n'
                '    """Remove newly added columns not in the expected schema."""\n'
                '    result = []\n'
                '    for row in rows:\n'
                '        filtered = {k: v for k, v in row.items() if k not in _NEW_COLUMNS}\n'
                '        result.append(filtered)\n'
                '    return result\n'
            )

        if added and removed and len(added) == len(removed):
            # Likely a rename
            rename_map = dict(zip(added, removed))
            map_str = ", ".join(f'{k!r}: {v!r}' for k, v in rename_map.items())
            return (
                '"""UASR Shim - Column Rename Mapping\n'
                'Maps renamed columns back to their original names.\n'
                f'Generated for drift: {_doc_safe(diagnosis.root_cause)}\n'
                '"""\n\n'
                f'_RENAME_MAP = {{{map_str}}}\n\n'
                'def transform(rows: list[dict]) -> list[dict]:\n'
                '    """Rename new column names back to expected names."""\n'
                '    result = []\n'
                '    for row in rows:\n'
                '        mapped = {}\n'
                '        for k, v in row.items():\n'
                '            new_key = _RENAME_MAP.get(k, k)\n'
                '            mapped[new_key] = v\n'
                '        result.append(mapped)\n'
                '    return result\n'
            )

        return None

    def _statistical_shim(self, drift_vector: Dict, diagnosis: DiagnosisResult) -> Optional[str]:
        affected = drift_vector.get("affected_columns", [])
        max_kl = drift_vector.get("max_kl", 0)
        zeta = drift_vector.get("threshold_zeta", 0.15)

        if not affected:
            return None

        # --- Deterministic rescale (unit-bug) heal --------------------------
        # If a column's batch mean is a near-integer multiple (or divisor) of
        # its baseline mean, the drift is a systematic unit-scale error
        # (e.g. cents mislabelled as dollars, x100). A divide-by-factor shim
        # heals it exactly -- this is value-level healing, no LLM needed.
        col_stats = drift_vector.get("col_stats", {})
        rescale_ops = {}
        for col in affected:
            cs = col_stats.get(col) or {}
            bmean = cs.get("baseline_mean")
            xmean = cs.get("batch_mean")
            if not bmean or not xmean or bmean == 0:
                continue
            ratio = xmean / bmean
            factor = None
            # Candidate scale factors: common unit bugs (10, 100, 1000, 60, ...).
            for cand in (10.0, 100.0, 1000.0, 1e6, 60.0, 3600.0, 24.0, 12.0, 2.0):
                if abs(ratio - cand) / cand < 0.05:       # batch inflated x cand
                    factor = cand
                    break
                if abs(ratio - 1.0 / cand) * cand < 0.05:  # batch deflated / cand
                    factor = 1.0 / cand
                    break
            if factor is not None:
                rescale_ops[col] = factor

        if rescale_ops:
            div_lines = "\n".join(
                f'        if {col!r} in row and isinstance(row[{col!r}], (int, float)):\n'
                f'            row[{col!r}] = row[{col!r}] / {factor!r}'
                for col, factor in rescale_ops.items()
            )
            factors_repr = ", ".join(f"{c}:x{f:g}" for c, f in rescale_ops.items())
            return (
                '"""UASR Shim - Unit Rescale (value-level heal)\n'
                f'Corrects systematic unit-scale error ({_doc_safe(factors_repr)}).\n'
                f'Generated for drift: {_doc_safe(diagnosis.root_cause)}\n'
                '"""\n\n'
                'def transform(rows: list[dict]) -> list[dict]:\n'
                '    """Divide mis-scaled columns back to baseline units."""\n'
                '    result = []\n'
                '    for row in rows:\n'
                f'{div_lines}\n'
                '        result.append(row)\n'
                '    return result\n'
            )

        if max_kl > zeta * 5:
            # Severe - clip each drifted column to its own baseline mean +/-
            # 3*std. Per-column (not one global pair) so a column with a
            # tight baseline isn't clipped to a wide neighbour's range.
            # A missing/zero baseline_std means we can't derive a safe band
            # for that column -- fall back to a wide no-op range instead of
            # collapsing every value in it to a single point.
            bounds: Dict[str, tuple] = {}
            for col in affected:
                cs = col_stats.get(col) or {}
                bmean = cs.get("baseline_mean")
                bstd = cs.get("baseline_std")
                if bmean is None or not bstd:
                    bounds[col] = (-1e9, 1e9)
                else:
                    bounds[col] = (bmean - 3 * bstd, bmean + 3 * bstd)

            clip_lines = "\n".join(
                f'        if {col!r} in row and isinstance(row[{col!r}], (int, float)):\n'
                f'            _lo, _hi = _CLIP_BOUNDS[{col!r}]\n'
                f'            row[{col!r}] = max(min(row[{col!r}], _hi), _lo)'
                for col in affected
            )
            bounds_repr = ", ".join(f'{c!r}: ({lo!r}, {hi!r})' for c, (lo, hi) in bounds.items())
            return (
                '"""UASR Shim - Outlier Clipping\n'
                'Clips extreme values to baseline mean +/- 3*std, per column.\n'
                f'Generated for drift: {_doc_safe(diagnosis.root_cause)}\n'
                '"""\n\n'
                f'_CLIP_BOUNDS = {{{bounds_repr}}}\n\n'
                'def transform(rows: list[dict]) -> list[dict]:\n'
                '    """Clip outlier values to each column\'s baseline range."""\n'
                '    result = []\n'
                '    for row in rows:\n'
                f'{clip_lines}\n'
                '        result.append(row)\n'
                '    return result\n'
            )
        elif drift_vector.get("detector") == "wasserstein_martingale":
            # A martingale alarm fired but neither the rescale heal above nor
            # the severe-clip branch found a fix (no col_stats-derived rescale
            # factor, and max_kl is always 0 for this detector so the clip
            # branch above never triggers either). A no-op pass-through shim
            # here would auto-deploy as "fixed" while the real drift the
            # martingale caught goes untouched (see BUG-033) -- return None so
            # _run falls through to LLM generation instead of committing a
            # vacuous template.
            return None
        else:
            # Mild - just log and pass through
            cols_str = ", ".join(f'{c!r}' for c in affected)
            return (
                '"""UASR Shim - Drift Monitor (pass-through)\n'
                'Logs statistical drift metrics without modifying data.\n'
                f'Generated for drift: {_doc_safe(diagnosis.root_cause)}\n'
                '"""\n\n'
                '_logger = logging.getLogger("uasr.shim.monitor")\n'
                f'_DRIFT_COLUMNS = [{cols_str}]\n\n'
                'def transform(rows: list[dict]) -> list[dict]:\n'
                '    """Log drift metrics and pass data through unchanged."""\n'
                '    _logger.warning(\n'
                f'        "Statistical drift in columns: %s (KL={max_kl:.4f}, zeta={zeta:.4f})",\n'
                '        _DRIFT_COLUMNS,\n'
                '    )\n'
                '    return rows\n'
            )

    @staticmethod
    def _location_shift_reason(drift_vector: Dict[str, Any]) -> Optional[str]:
        """Detect a location shift: the batch's own mean sits many
        baseline-sigmas from the baseline mean, meaning the whole
        distribution moved rather than a few extreme values contaminating
        an otherwise-centered batch. Uses the same +/-3*std band the clip
        shim would use as bounds -- if the batch centroid itself falls
        outside that band, clipping has nothing sensible left to preserve.
        """
        affected = drift_vector.get("affected_columns", [])
        col_stats = drift_vector.get("col_stats", {})
        for col in affected:
            cs = col_stats.get(col) or {}
            bmean = cs.get("baseline_mean")
            bstd = cs.get("baseline_std")
            xmean = cs.get("batch_mean")
            if bmean is None or not bstd or xmean is None:
                continue
            shift_sigmas = abs(xmean - bmean) / bstd
            if shift_sigmas > 3:
                return (
                    f"column '{col}': batch mean {xmean:.4f} is {shift_sigmas:.1f} "
                    f"baseline-sigmas from the baseline mean {bmean:.4f} "
                    f"(baseline_std={bstd:.4f}) -- a location shift, not outlier "
                    "contamination."
                )
        return None

    def _escalation_shim(self, diagnosis: DiagnosisResult, reason: str) -> str:
        """No-op audit shim for drift routed to human approval (see _run).

        Does not transform data. A human approves or rejects via
        POST /uasr/recovery/{id}/approve|reject after reviewing `reason`.
        """
        return (
            '"""UASR Shim - Escalated (no auto-transform)\n'
            f'Reason: {_doc_safe(reason)}\n'
            f'Generated for drift: {_doc_safe(diagnosis.root_cause)}\n'
            'Held for human approval -- see GET /uasr/recovery/pending.\n'
            '"""\n\n'
            '_logger = logging.getLogger("uasr.shim.escalated")\n\n'
            'def transform(rows: list[dict]) -> list[dict]:\n'
            '    """No-op: awaiting human approval, does not modify data."""\n'
            f'    _logger.warning({reason!r})\n'
            '    return rows\n'
        )

    # ────────────────────────────────────────────────────────────────
    # LLM-assisted shim generation
    # ────────────────────────────────────────────────────────────────

    async def _llm_shim(
        self,
        drift_type: str,
        drift_vector: Dict[str, Any],
        diagnosis: DiagnosisResult,
    ) -> Optional[str]:
        """Use LLM to generate a shim for complex drift patterns."""
        try:
            from shared.llm_provider import get_llm

            llm = get_llm()
            if not llm or not llm.is_available():
                return None

            prompt = [
                (
                    "You are a data engineer writing a Python transformation function. "
                    "Generate a self-contained Python function called `transform(rows: list[dict]) -> list[dict]` "
                    "that fixes the following data drift issue. The function receives a list of row dicts "
                    "and must return the corrected list. Include a module docstring. "
                    "Do NOT use any external libraries. Only use the Python standard library. "
                    "Return ONLY the Python code, no markdown fences."
                ),
                json.dumps({
                    "drift_type": drift_type,
                    "root_cause": diagnosis.root_cause,
                    "hypothesis": diagnosis.hypothesis,
                    "suggested_action": diagnosis.suggested_action,
                    "drift_vector": drift_vector,
                }),
            ]

            code = await asyncio.to_thread(llm.generate, prompt)
            if code and "def transform" in code:
                # Strip markdown fences if present
                cleaned = code.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1]
                if cleaned.endswith("```"):
                    cleaned = cleaned.rsplit("```", 1)[0]
                return cleaned.strip()
        except Exception as exc:
            logger.warning("LLM shim generation failed: %s", exc)

        return None

    # ────────────────────────────────────────────────────────────────
    # Fallback shim
    # ────────────────────────────────────────────────────────────────

    def _fallback_shim(self, drift_type: str, diagnosis: DiagnosisResult) -> str:
        """Last-resort: generate a pass-through shim that logs the issue."""
        return textwrap.dedent(f'''\
            """UASR Shim — Fallback (pass-through with logging)
            Could not generate a specific fix for: {_doc_safe(diagnosis.root_cause)}
            Manual intervention may be required.
            """

            import logging
            _logger = logging.getLogger("uasr.shim.fallback")

            def transform(rows: list[dict]) -> list[dict]:
                """Pass data through and log the unresolved drift."""
                _logger.warning(
                    "UASR fallback shim active — drift_type=%s, cause=%s",
                    {str(drift_type)!r},
                    {diagnosis.root_cause[:200]!r},
                )
                return rows
        ''')


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _doc_safe(text: Any) -> str:
    """Make untrusted text safe to place inside a generated shim's docstring.

    BUG-265 (residual of BUG-073): column names and the LLM-supplied root cause were
    spliced raw between the triple quotes of the generated module docstring, so a
    value containing three double quotes closed the docstring and the rest of it
    became shim code. Quotes and backslashes are replaced and the text is put on one
    line; anywhere the value is used as DATA it is emitted with ``!r`` instead."""
    return " ".join(str(text).replace("\\", "/").replace('"', "'").split())


def _template_name(shim_code: str) -> str:
    first = shim_code.split("\n", 1)[0]
    return first.replace('"""UASR Shim - ', "").replace('"""UASR Shim — ', "").strip() or "generated"


def _python_cast(dtype: str) -> str:
    """Map a dtype string to a Python type constructor name."""
    dtype_lower = dtype.lower()
    if "int" in dtype_lower:
        return "int"
    if "float" in dtype_lower or "double" in dtype_lower or "numeric" in dtype_lower or "decimal" in dtype_lower:
        return "float"
    if "bool" in dtype_lower:
        return "bool"
    return "str"


# ────────────────────────────────────────────────────────────────────
# Proven repairs (ADR-002, "What proven means for each repair")
# --------------------------------------------------------------------
# Pure functions over one batch and the source's baseline profile. Each either
# returns a repair with the evidence that its rule was met, or says why it cannot
# be proven -- it never picks the likeliest answer. The caller
# (RecoveryLoop.prove_repairs) quarantines whatever is not proven.
# ────────────────────────────────────────────────────────────────────

_RENAME_MATCH = 0.99            # ADR-002: >= 99% of values fit the baseline column
_RENAME_CLOSE = 0.90            # a second candidate this good makes a match ambiguous
_MIN_FINGERPRINT_VALUES = 20    # fewer values cannot tell two columns apart
_SCALE_INSIDE = 0.999           # ADR-002: >= 99.9% of values back inside the baseline range
_SCALE_POWERS = tuple(n for n in range(-9, 10) if n != 0)
_SCALE_NEIGHBOURS = (0.5, 0.8, 1.25, 2.0)  # factors a proven power of ten must beat
# The baseline's range is the min/max of a finite sample, so clean values land past it
# (about 2n/(n0+1) of n values -- more than 0.1% for any batch of real size): widen it
# by half its span on each side. A wrong power of ten still lands far outside, and the
# moments, histogram and neighbour checks carry the discrimination.
_RANGE_MARGIN = 0.5

_INT_TYPES = {"int", "integer", "int8", "int16", "int32", "int64", "bigint", "smallint", "tinyint", "long"}
_FLOAT_TYPES = {"float", "double", "float32", "float64", "real", "double precision"}
_BOOL_TYPES = {"bool", "boolean"}
_STR_TYPES = {"str", "string", "text", "varchar", "char", "object"}
_INT_LITERAL = re.compile(r"[+-]?\d+")
_DECIMAL_LITERAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and math.isfinite(v)


def _normalised_name(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _baseline_range(dist: ColumnDistribution) -> Optional[Tuple[float, float]]:
    edges = DriftDetector._baseline_edges(dist.histogram or {})
    if edges is None or dist.mean is None:
        return None
    lo, hi = edges[0], edges[-1]
    margin = _RANGE_MARGIN * (hi - lo) or 1e-9 * max(1.0, abs(lo))
    return lo - margin, hi + margin


def _moments_match(values: List[float], dist: ColumnDistribution) -> bool:
    """Mean and spread agree with the baseline's within five standard errors of the
    two samples, so a correct repair fails this about once in a million batches."""
    n = len(values)
    n0 = max(int(dist.sample_size or 0), 1)
    mu, sigma = float(dist.mean or 0.0), float(dist.std or 0.0)
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    if sigma <= 1e-12:
        return abs(mean - mu) <= 1e-9 * max(1.0, abs(mu))
    if abs(mean - mu) > 5 * sigma * math.sqrt(1 / n + 1 / n0):
        return False
    if n < 2:
        return True
    ratio = float(arr.std()) / sigma
    t = 5 * math.sqrt(1 / (2 * n) + 1 / (2 * n0)) + 0.05
    return 1 / (1 + t) <= ratio <= 1 + t


def _fits_baseline(values: List[float], dist: ColumnDistribution) -> Tuple[float, bool]:
    """(share inside the baseline's range, whether the values are indistinguishable
    from the baseline: >= 99.9% inside, same moments, and the detector's own
    aligned-histogram G test finds no difference)."""
    bounds = _baseline_range(dist)
    edges = DriftDetector._baseline_edges(dist.histogram or {})
    if bounds is None or edges is None or not values:
        return 0.0, False
    arr = np.asarray(values, dtype=float)
    inside = float(((arr >= bounds[0]) & (arr <= bounds[1])).mean())
    if inside < _SCALE_INSIDE or not _moments_match(values, dist):
        return inside, False
    _, significant = DriftDetector._aligned_kl(
        DriftDetector._histogram_on_edges(arr, edges)["counts"], dist.histogram["counts"])
    return inside, not significant


def fingerprint(values: List[Any], dist: Optional[ColumnDistribution]) -> Tuple[float, bool]:
    """(share of values that fit the baseline column, whether their distribution matches it).

    "Fit" means the same kind -- numeric against a numeric baseline, text against a
    categorical one -- and inside its support: the baseline's range, or its set of
    categories. The distribution test is the detector's own aligned-histogram G test,
    so a column matches only when the drift check itself would call it unchanged.
    """
    if not values or dist is None:
        return 0.0, False
    hist = dist.histogram or {}
    edges = DriftDetector._baseline_edges(hist)
    bounds = _baseline_range(dist)
    if edges is not None and bounds is not None:
        lo, hi = bounds
        nums = [float(v) for v in values if _is_number(v)]
        support = sum(1 for v in nums if lo <= v <= hi) / len(values)
        if len(nums) != len(values):
            return support, False
        _, significant = DriftDetector._aligned_kl(
            DriftDetector._histogram_on_edges(nums, edges)["counts"], hist["counts"])
        return support, not significant and _moments_match(nums, dist)
    cats = hist.get("categories")
    if isinstance(cats, dict) and cats:
        keys = list(cats)
        counts = Counter(str(v) for v in values if not _is_number(v))
        inside = sum(counts.get(k, 0) for k in keys)
        _, significant = DriftDetector._aligned_kl(
            [0, *(counts.get(k, 0) for k in keys), len(values) - inside], [cats[k] for k in keys])
        return inside / len(values), not significant
    return 0.0, False


def prove_renames(
    added: List[str],
    removed: List[str],
    rows: List[Dict[str, Any]],
    baseline: Dict[str, ColumnDistribution],
) -> Tuple[Dict[str, Tuple[str, Dict[str, Any]]], List[str]]:
    """Pair new columns with removed ones, only where ADR-002's rename rule is met.

    New column ``a`` is renamed back to removed column ``r`` when ``a`` matches ``r``
    -- by normalised name, or by value fingerprint (>= 99% of values fit ``r`` and the
    distribution matches) -- and nothing else comes close: ``a`` matches no other
    removed column, no other new column matches ``r``, and no column on either side
    fits >= 90%. Returns ``({new: (old, proof)}, reasons)``, one reason per removed
    column left unpaired. A new column left unpaired is simply a new column.
    """
    values = {a: [r.get(a) for r in rows if r.get(a) is not None] for a in added}
    matches: Dict[str, Dict[str, Dict[str, Any]]] = {a: {} for a in added}
    close: Dict[str, set] = {a: set() for a in added}
    for a in added:
        for r in removed:
            by_name = bool(_normalised_name(a)) and _normalised_name(a) == _normalised_name(r)
            ratio, dist_ok = 0.0, False
            if len(values[a]) >= _MIN_FINGERPRINT_VALUES and r in baseline:
                ratio, dist_ok = fingerprint(values[a], baseline[r])
            if by_name or (ratio >= _RENAME_MATCH and dist_ok):
                matches[a][r] = {
                    "rule": "normalised_name" if by_name else "value_fingerprint",
                    "from": a,
                    "match_ratio": round(ratio, 6),
                    "distribution_match": dist_ok,
                }
            elif ratio >= _RENAME_CLOSE:
                close[a].add(r)

    renames: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    reasons: List[str] = []
    for r in removed:
        claimants = sorted(a for a in added if r in matches[a] or r in close[a])
        if len(claimants) == 1:
            a = claimants[0]
            if r in matches[a] and len(matches[a]) == 1 and not close[a]:
                renames[a] = (r, matches[a][r])
                continue
        if not claimants:
            reasons.append(
                f"column '{r}' is missing from the batch and no new column matches it by name "
                "or by values; restoring it would be a guess")
        else:
            reasons.append(
                f"column '{r}' is missing and its rename is ambiguous (candidates {claimants}); "
                "ADR-002 needs exactly one match with no close second")
    return renames, reasons


def canonical_type(dtype: Any) -> Optional[str]:
    """The cast target for a baseline type name, or None when no lossless cast is defined."""
    d = str(dtype).strip().lower()
    if d in _INT_TYPES:
        return "int"
    if d in _FLOAT_TYPES:
        return "float"
    if d == "number":
        return "number"
    if d in _BOOL_TYPES:
        return "bool"
    if d in _STR_TYPES:
        return "str"
    return None


def _exact_float(s: str) -> Optional[float]:
    if not _DECIMAL_LITERAL.fullmatch(s):
        return None
    x = float(s)
    # repr(x) is the shortest text that round-trips x, so equal decimal values mean
    # the text held no digit the float cannot carry ("0.10000000000000001" fails).
    if not math.isfinite(x) or Decimal(repr(x)) != Decimal(s):
        return None
    return x


def cast_losslessly(value: Any, target: str) -> Tuple[bool, Any]:
    """Cast ``value`` back to ``target`` only if the cast round-trips exactly (ADR-002).

    Returns ``(True, cast_value)`` or ``(False, None)``. None stays None: a missing
    value is the null-rate check's business, not a cast. Text with more than one
    reading ("yes" or "1" for a bool, "007" or "+5" for an int) does not round-trip,
    so it is never cast.
    """
    if value is None:
        return True, None
    if target == "int":
        if _is_number(value) and isinstance(value, int):
            return True, value
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return True, int(value)
        if isinstance(value, str) and _INT_LITERAL.fullmatch(value) and str(int(value)) == value:
            return True, int(value)
        return False, None
    if target in ("float", "number"):
        if isinstance(value, float):
            return (True, value) if math.isfinite(value) else (False, None)
        if _is_number(value):
            if target == "number":
                return True, value
            return (True, float(value)) if int(float(value)) == value else (False, None)
        if isinstance(value, str):
            if target == "number" and _INT_LITERAL.fullmatch(value) and str(int(value)) == value:
                return True, int(value)
            x = _exact_float(value)
            return (True, x) if x is not None else (False, None)
        return False, None
    if target == "bool":
        if isinstance(value, bool):
            return True, value
        if isinstance(value, str) and value in ("true", "false", "True", "False"):
            return True, value.lower() == "true"
        return False, None
    if target == "str":
        if isinstance(value, str):
            return True, value
        if isinstance(value, bool) or _is_number(value):
            return True, repr(value) if isinstance(value, float) else str(value)
        return False, None
    return False, None


def restore_scaled(value: Any, power: int) -> Optional[float]:
    """The value that, scaled by 10**power, gives exactly ``value`` -- or None.

    Floating-point scaling is not exactly reversible (0.07 * 100 is 7.000000000000001),
    so the plain quotient can miss the original by an ulp. The shortest decimal near
    the quotient is tried first, and a candidate is accepted only if re-applying the
    fault reproduces ``value`` bit for bit.
    """
    factor = 10 ** abs(power)
    if isinstance(value, int):
        if power < 0:
            return value * factor
        q, rem = divmod(value, factor)
        if rem == 0:
            return q
    approx = value / factor if power > 0 else value * factor
    for cand in (float(f"{approx:.15g}"), approx):
        forward = (cand * factor,) if power > 0 else (cand / factor, cand * 10.0 ** power)
        if value in forward:
            return cand
    return None


def in_baseline_range(value: float, dist: ColumnDistribution) -> bool:
    bounds = _baseline_range(dist)
    return bounds is not None and bounds[0] <= value <= bounds[1]


def prove_scale(values: List[Any], dist: Optional[ColumnDistribution]) -> Tuple[Optional[int], Dict[str, Any]]:
    """Find the one power of ten that explains a drifted numeric column (ADR-002).

    Returns ``(power, proof)`` when exactly one factor 10**n (n = -9..9, n != 0) brings
    >= 99.9% of the values inside the baseline's range with the baseline's mean,
    spread and histogram; the unscaled values do not fit; and no factor near it
    (``_SCALE_NEIGHBOURS``) fits either. Otherwise ``(None, {"reason": ...})``.
    """
    if dist is None or _baseline_range(dist) is None:
        return None, {"reason": "the baseline column is not numeric, so no unit factor applies"}
    if not values or not all(_is_number(v) for v in values):
        return None, {"reason": "the column holds non-numeric values, so no unit factor applies"}
    nums = np.asarray([float(v) for v in values], dtype=float)

    def fit(factor: float) -> Tuple[float, bool]:
        return _fits_baseline(list(nums / factor), dist)

    if fit(1.0)[1]:
        return None, {"reason": "the values already fit the baseline unscaled, so the drift is not a unit error"}
    results = {n: fit(10.0 ** n) for n in _SCALE_POWERS}
    passing = [n for n, (_, ok) in results.items() if ok]
    if len(passing) != 1:
        return None, {"reason": (
            "no power of ten brings the values back inside the baseline" if not passing
            else f"more than one factor fits ({[10.0 ** n for n in passing]}), so the scale is ambiguous"
        )}
    n = passing[0]
    # "No other factor does": a batch that also fits at 0.8x or 1.25x the factor does
    # not pin the factor down -- a x7 or x12 fault would pass as x10 (ADR-002).
    near = [m for m in _SCALE_NEIGHBOURS if fit(10.0 ** n * m)[1]]
    if near:
        return None, {"reason": (
            f"factor {10.0 ** n:g} fits, but so do {[10.0 ** n * m for m in near]}; "
            "the values do not pin the scale down")}
    return n, {
        "rule": "unit_scale",
        "factor": 10.0 ** n,
        "inside_ratio": round(results[n][0], 6),
        "runner_up_inside_ratio": round(max(r for m, (r, _) in results.items() if m != n), 6),
        "neighbours_rejected": [10.0 ** n * m for m in _SCALE_NEIGHBOURS],
    }
