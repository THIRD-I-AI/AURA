"""BUG-265 (residual of BUG-073): untrusted text was spliced raw into the triple-quoted
docstring of generated shim source. A column name or an LLM-supplied root cause
containing three double quotes closed the docstring, and the rest became shim code."""
from __future__ import annotations

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.actuator_agent import SynthesisActuatorAgent
from uasr.models import DiagnosisResult

# Closes a docstring, plants a statement, and re-opens one so the source still parses.
BREAKOUT = 'x"""\nPWNED = __import__("os").getcwd()\n"""'
EVIL_COL = 'amount"""\nPWNED = 1\n"""'


@pytest.fixture
def actuator():
    return SynthesisActuatorAgent()


@pytest.fixture
def evil_diag():
    return DiagnosisResult(drift_event_id="e1", root_cause=BREAKOUT)


def _top_level_names(code: str) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(code).body:
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _stat(col: str, batch_mean: float, max_kl: float) -> dict:
    return {
        "affected_columns": [col], "max_kl": max_kl, "threshold_zeta": 0.15,
        "col_stats": {col: {"baseline_mean": 50.0, "batch_mean": batch_mean, "baseline_std": 12.0}},
    }


def test_doc_safe_removes_everything_that_can_close_or_escape_a_docstring():
    from uasr.actuator_agent import _doc_safe

    cleaned = _doc_safe('a"""b\\c\nd')
    assert '"' not in cleaned and "\\" not in cleaned and "\n" not in cleaned


@pytest.mark.parametrize("build", [
    lambda a, d: a._statistical_shim(_stat("amount", 5000.0, 25.0), d),   # unit rescale
    lambda a, d: a._statistical_shim(_stat("amount", 65.0, 5.0), d),      # clip
    lambda a, d: a._statistical_shim({"affected_columns": ["v"], "max_kl": 0.3, "threshold_zeta": 0.15}, d),  # monitor
    lambda a, d: a._fallback_shim("statistical", d),
], ids=["rescale", "clip", "monitor", "fallback"])
def test_a_root_cause_cannot_break_out_of_the_docstring(actuator, evil_diag, build):
    code = build(actuator, evil_diag)
    assert code, "this branch should produce a shim"
    assert "PWNED" not in _top_level_names(code)
    # the planted call must not exist as code anywhere in the module
    assert not any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "__import__"
                   for n in ast.walk(ast.parse(code)))


def test_a_column_name_cannot_break_out_of_the_rescale_docstring(actuator):
    diag = DiagnosisResult(drift_event_id="e1", root_cause="unit bug")
    code = actuator._statistical_shim(_stat(EVIL_COL, 5000.0, 25.0), diag)
    assert "Unit Rescale" in code
    assert "PWNED" not in _top_level_names(code)
