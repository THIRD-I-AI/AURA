"""
BAVT — Budget-Aware Value Tree
==============================
Translates the BATS pivot signal from "advisory directive in a prompt"
into a hard routing decision: when the live tracker's
``tokens_remaining`` can't cover an optional node's projected cost,
the orchestrator skips that node and emits a structured "skipped:
BAVT pivot" record instead of letting the LLM hit the context cutoff.

Each node has a ``cost`` prior (rolling mean tokens it has historically
consumed) and a ``value`` prior in [0, 1] reflecting how much it
contributes to the final answer. Required nodes (``planner``,
``sql_run``, ``exec_run``) always run; optional nodes
(``viz_run``, ``analysis_run``) are dropped greedily by descending
value when the remaining budget is too tight to keep them all.

Operators tune priors at deploy time via two env JSON dicts:

    AURA_BAVT_COSTS='{"viz_run": 1500, "analysis_run": 2500}'
    AURA_BAVT_VALUES='{"viz_run": 0.6, "analysis_run": 0.7}'

Self-tuning the means from real Prometheus counters is intentionally
deferred — fixed priors for the canonical 5-node DAG are accurate
enough that the pivot is meaningful from day one.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Set

logger = logging.getLogger("aura.shared.bavt")


@dataclass(frozen=True)
class BAVTNode:
    name: str
    required: bool
    cost_tokens: int
    value: float


# Defaults are conservative on cost (so BAVT errs toward dropping) and
# reflect that an analyst typically prefers a *narrative answer* (analysis)
# over a *chart* (viz) when forced to choose between them.
_DEFAULT_NODES: Dict[str, BAVTNode] = {
    "planner":      BAVTNode("planner",      required=True,  cost_tokens=2000, value=1.0),
    "sql_run":      BAVTNode("sql_run",      required=True,  cost_tokens=2500, value=1.0),
    "exec_run":     BAVTNode("exec_run",     required=True,  cost_tokens=0,    value=1.0),
    "viz_run":      BAVTNode("viz_run",      required=False, cost_tokens=1500, value=0.6),
    "analysis_run": BAVTNode("analysis_run", required=False, cost_tokens=2500, value=0.7),
}


def _load_overrides(env_var: str) -> Dict[str, float]:
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return {}
    try:
        return {str(k): float(v) for k, v in json.loads(raw).items()}
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("BAVT %s ignored (invalid JSON): %s", env_var, exc)
        return {}


def _resolve_tree() -> Dict[str, BAVTNode]:
    """Apply env overrides on top of the default priors."""
    cost_overrides = _load_overrides("AURA_BAVT_COSTS")
    value_overrides = _load_overrides("AURA_BAVT_VALUES")
    if not cost_overrides and not value_overrides:
        return _DEFAULT_NODES
    out: Dict[str, BAVTNode] = {}
    for name, node in _DEFAULT_NODES.items():
        out[name] = BAVTNode(
            name=name,
            required=node.required,
            cost_tokens=int(cost_overrides.get(name, node.cost_tokens)),
            value=float(value_overrides.get(name, node.value)),
        )
    return out


def affordable_optional_nodes(remaining_tokens: int, candidates: Iterable[str]) -> Set[str]:
    """Pick the highest-value subset of ``candidates`` (optional nodes only)
    whose cumulative cost fits in ``remaining_tokens``. Greedy by value
    desc — for two optional nodes this is exact; for larger trees it's
    a near-optimal knapsack approximation."""
    tree = _resolve_tree()
    optional = sorted(
        (tree[n] for n in candidates if n in tree and not tree[n].required),
        key=lambda x: x.value,
        reverse=True,
    )
    keep: Set[str] = set()
    budget = max(remaining_tokens, 0)
    for node in optional:
        if node.cost_tokens <= budget:
            keep.add(node.name)
            budget -= node.cost_tokens
    return keep


def can_afford(node_name: str) -> Optional[bool]:
    """Should the orchestrator run ``node_name`` given the live BATS tracker?

    Returns:
      * ``None`` when BATS isn't bound to this run (no tracker on the
        contextvar). Callers treat ``None`` as "run normally" — BAVT only
        forces pivots when the operator explicitly opted into a budget.
      * ``True``  when the node is required, or its projected cost fits
        in ``tracker.tokens_remaining``.
      * ``False`` when the node is optional and its cost exceeds what
        remains. The orchestrator should emit a ``skipped`` record and
        route to the next node (or END).
    """
    from shared.budget import current_budget

    tracker = current_budget()
    if tracker is None:
        return None
    tree = _resolve_tree()
    node = tree.get(node_name)
    if node is None or node.required:
        return True
    return node.cost_tokens <= tracker.tokens_remaining
