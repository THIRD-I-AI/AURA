"""Regression tests for deterministic (template) shim generation.

These guard three defects fixed in the LLM-free recovery path:

  1. textwrap.dedent + f-string interpolation indent mismatch produced
     syntactically invalid shim code (unexpected indent).
  2. Two-dict exec() in the sandbox executor left module-level shim
     constants (_CLIP_MAX, _NEW_COLUMNS, _RENAME_MAP, _logger) unresolved
     inside transform().
  3. A x100 unit-scale (unit-bug) drift had no deterministic heal; the
     clip shim could not correct it. A rescale branch now heals it exactly.
"""
import ast

import pytest

from uasr.actuator_agent import SynthesisActuatorAgent
from uasr.models import DiagnosisResult
from uasr.recovery_loop import RecoveryLoop


@pytest.fixture
def actuator():
    return SynthesisActuatorAgent.__new__(SynthesisActuatorAgent)


@pytest.fixture
def diag():
    return DiagnosisResult(drift_event_id="e1", root_cause="test drift")


def _compiles_and_defines_transform(code: str):
    """A shim must parse, compile, and define a callable transform()."""
    assert code and "def transform" in code
    ast.parse(code)                       # defect #1: valid Python
    ns: dict = {}
    exec(compile(code, "<shim>", "exec"), ns)  # noqa: S102
    fn = ns.get("transform")
    assert callable(fn)
    return fn


# --- defect #1: every template compiles to valid Python -------------------

def test_schema_type_change_shim_compiles(actuator, diag):
    dv = {"type": "type_change", "old_types": {"age": "int"},
          "new_types": {"age": "str"}}
    fn = _compiles_and_defines_transform(actuator._schema_shim(dv, diag))
    out = fn([{"age": "42"}])
    assert out == [{"age": 42}]


def test_schema_removed_column_shim_compiles(actuator, diag):
    dv = {"type": "schema_change", "removed": ["email"], "added": []}
    fn = _compiles_and_defines_transform(actuator._schema_shim(dv, diag))
    out = fn([{"name": "x"}])
    assert out == [{"name": "x", "email": None}]


def test_schema_added_column_shim_compiles(actuator, diag):
    dv = {"type": "schema_change", "added": ["debug"], "removed": []}
    fn = _compiles_and_defines_transform(actuator._schema_shim(dv, diag))
    out = fn([{"name": "x", "debug": 1}])
    assert out == [{"name": "x"}]


def test_schema_rename_shim_compiles(actuator, diag):
    dv = {"type": "schema_change", "added": ["amt"], "removed": ["amount"]}
    fn = _compiles_and_defines_transform(actuator._schema_shim(dv, diag))
    out = fn([{"amt": 10}])
    assert out == [{"amount": 10}]


def test_statistical_clip_shim_compiles(actuator, diag):
    # Severe KL, no col_stats -> falls through to clip branch.
    dv = {"affected_columns": ["v"], "max_kl": 5.0, "threshold_zeta": 0.15}
    fn = _compiles_and_defines_transform(actuator._statistical_shim(dv, diag))
    out = fn([{"v": 3}])
    assert out == [{"v": 3}]  # within +/-1e9 clip bounds


def test_statistical_monitor_shim_compiles(actuator, diag):
    # Mild KL -> pass-through monitor branch (uses module-level _logger).
    dv = {"affected_columns": ["v"], "max_kl": 0.3, "threshold_zeta": 0.15}
    fn = _compiles_and_defines_transform(actuator._statistical_shim(dv, diag))
    out = fn([{"v": 7}])
    assert out == [{"v": 7}]


# --- defect #3: deterministic rescale heal for unit-bug drift -------------

@pytest.mark.parametrize("factor,batch_mean", [(100.0, 5000.0), (1000.0, 50000.0),
                                                (60.0, 3000.0)])
def test_statistical_rescale_shim_heals_unit_bug(actuator, diag, factor, batch_mean):
    dv = {
        "affected_columns": ["amount"], "max_kl": 25.0, "threshold_zeta": 0.15,
        "col_stats": {"amount": {"baseline_mean": 50.0, "batch_mean": batch_mean,
                                 "baseline_std": 12.0}},
    }
    code = actuator._statistical_shim(dv, diag)
    assert "Unit Rescale" in code
    fn = _compiles_and_defines_transform(code)
    out = fn([{"amount": batch_mean}])
    assert out[0]["amount"] == pytest.approx(batch_mean / factor)


def test_rescale_ignored_when_ratio_not_unit_scale(actuator, diag):
    # 1.3x is not a recognised unit-scale factor -> no rescale, falls to clip.
    dv = {
        "affected_columns": ["amount"], "max_kl": 5.0, "threshold_zeta": 0.15,
        "col_stats": {"amount": {"baseline_mean": 50.0, "batch_mean": 65.0,
                                 "baseline_std": 12.0}},
    }
    code = actuator._statistical_shim(dv, diag)
    assert "Unit Rescale" not in code


# --- defect #2: sandbox executor resolves module-level constants ----------

def test_sandbox_execute_resolves_module_constants():
    """A shim with module-level constants used inside transform() must run.

    Previously exec(code, globals, locals) put the constants in locals while
    transform().__globals__ was globals, so the names failed to resolve.
    """
    shim = (
        "_CLIP_MIN = -10\n"
        "_CLIP_MAX = 10\n"
        "def transform(rows):\n"
        "    return [{'v': max(min(r['v'], _CLIP_MAX), _CLIP_MIN)} for r in rows]\n"
    )
    out = RecoveryLoop._sandbox_execute(shim, [{"v": 999}, {"v": -999}])
    assert out == [{"v": 10}, {"v": -10}]
