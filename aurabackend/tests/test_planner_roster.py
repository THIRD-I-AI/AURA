"""DSR-011 regression: the planner's LLM-facing AGENT_ROSTER must list every
agent type DAGExecutor can actually run. A plan can only ever target an
agent named in the roster, so any AGENT_MAP entry missing from the roster
is silently unreachable from a generated plan."""
from agents.executor import AGENT_MAP
from agents.planner import AGENT_ROSTER


def test_agent_roster_covers_every_executable_agent():
    missing = set(AGENT_MAP.keys()) - set(AGENT_ROSTER.keys())
    assert not missing, f"AGENT_ROSTER is missing agents DAGExecutor can run: {missing}"


def test_agent_roster_has_no_unknown_agents():
    unknown = set(AGENT_ROSTER.keys()) - set(AGENT_MAP.keys())
    assert not unknown, f"AGENT_ROSTER references agents DAGExecutor cannot run: {unknown}"
