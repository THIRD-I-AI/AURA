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

import json
import logging
import os
import sys
import textwrap
from typing import Any, Dict, Optional, cast

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity
from agents.params import ActuatorAgentParams
from uasr.models import DiagnosisResult, DriftType, ShimResult

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
                        f'        if "{col}" in row:\n'
                        f'            try:\n'
                        f'                row["{col}"] = {_python_cast(old_t)}(row["{col}"])\n'
                        f'            except (ValueError, TypeError):\n'
                        f'                row["{col}"] = None  # Could not cast, set to None'
                    )

            if not cast_lines:
                return None

            body = "\n".join(cast_lines)
            return (
                '"""UASR Shim - Type Cast Recovery\n'
                'Converts changed column types back to expected types.\n'
                f'Generated for drift: {diagnosis.root_cause}\n'
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
                f'        row.setdefault("{col}", None)'
                for col in removed
            )
            return (
                '"""UASR Shim - Missing Column Recovery\n'
                'Adds default values for columns removed from upstream.\n'
                f'Generated for drift: {diagnosis.root_cause}\n'
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
            keep_cols_str = ", ".join(f'"{c}"' for c in added)
            return (
                '"""UASR Shim - Column Filter\n'
                'Strips unexpected new columns to maintain schema compatibility.\n'
                f'Generated for drift: {diagnosis.root_cause}\n'
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
            map_str = ", ".join(f'"{k}": "{v}"' for k, v in rename_map.items())
            return (
                '"""UASR Shim - Column Rename Mapping\n'
                'Maps renamed columns back to their original names.\n'
                f'Generated for drift: {diagnosis.root_cause}\n'
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
                f'        if "{col}" in row and isinstance(row["{col}"], (int, float)):\n'
                f'            row["{col}"] = row["{col}"] / {factor!r}'
                for col, factor in rescale_ops.items()
            )
            factors_repr = ", ".join(f"{c}:x{f:g}" for c, f in rescale_ops.items())
            return (
                '"""UASR Shim - Unit Rescale (value-level heal)\n'
                f'Corrects systematic unit-scale error ({factors_repr}).\n'
                f'Generated for drift: {diagnosis.root_cause}\n'
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
            # Severe - clip to baseline percentiles
            clip_lines = "\n".join(
                f'        if "{col}" in row and isinstance(row["{col}"], (int, float)):\n'
                f'            row["{col}"] = max(min(row["{col}"], _CLIP_MAX), _CLIP_MIN)'
                for col in affected
            )
            return (
                '"""UASR Shim - Outlier Clipping\n'
                'Clips extreme values to baseline distribution percentiles.\n'
                f'Generated for drift: {diagnosis.root_cause}\n'
                '"""\n\n'
                '# These should be populated from the baseline distribution\n'
                '_CLIP_MIN = -1e9\n'
                '_CLIP_MAX = 1e9\n\n'
                'def transform(rows: list[dict]) -> list[dict]:\n'
                '    """Clip outlier values to safe range."""\n'
                '    result = []\n'
                '    for row in rows:\n'
                f'{clip_lines}\n'
                '        result.append(row)\n'
                '    return result\n'
            )
        else:
            # Mild - just log and pass through
            cols_str = ", ".join(f'"{c}"' for c in affected)
            return (
                '"""UASR Shim - Drift Monitor (pass-through)\n'
                'Logs statistical drift metrics without modifying data.\n'
                f'Generated for drift: {diagnosis.root_cause}\n'
                '"""\n\n'
                'import logging\n'
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

            code = llm.generate(prompt)
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
            Could not generate a specific fix for: {diagnosis.root_cause}
            Manual intervention may be required.
            """

            import logging
            _logger = logging.getLogger("uasr.shim.fallback")

            def transform(rows: list[dict]) -> list[dict]:
                """Pass data through and log the unresolved drift."""
                _logger.warning(
                    "UASR fallback shim active — drift_type=%s, cause=%s",
                    "{drift_type}",
                    """{diagnosis.root_cause[:200]}""",
                )
                return rows
        ''')


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

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
