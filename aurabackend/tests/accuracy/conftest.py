"""The heal_realism lanes (BUG-577) run only when ``-m`` names them.

They hold the ADR-002 gates ((a) applied-fix precision >= 0.999, (d) zero clean
false alarms; see harness.py) on real datasets and multi-batch streams, and report
(b), the strict accuracy that also counts faults no check sees on real data
(undetected exact-copy duplicates on keyless data, faults in sparse columns --
BUG-577's findings). They need sklearn and statsmodels, which only the causal
extras install, and run for many minutes, so they are their own CI job and are
deselected here -- counted as deselected in the summary, never silently passed --
unless the run asks for them:

    python -m pytest tests/accuracy -m heal_realism -s
"""
from __future__ import annotations


def pytest_collection_modifyitems(config, items):
    if "heal_realism" in (config.getoption("markexpr") or ""):
        return
    keep, drop = [], []
    for item in items:
        (drop if item.get_closest_marker("heal_realism") else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
