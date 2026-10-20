"""Every tool call an agent makes matches the tool it calls.

Closes the class behind BUG-437: agents called tools with argument names the tools
do not take (filepath= for file_path, connection= for connector_type/config), the
call raised, the error was swallowed, and the feature never worked. This test reads
every ``tools.call("<name>", ...)`` in the agents package and checks it against the
registered tool: the tool exists, every keyword is one it accepts, and every
argument it requires is given.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from agents.tool_registry import ToolRegistry
from agents.tools import register_all_tools

_AGENTS = Path(__file__).resolve().parents[1] / "agents"


def _tool_calls():
    for path in sorted(_AGENTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            if not (isinstance(node, ast.Call) and isinstance(func, ast.Attribute) and func.attr == "call"):
                continue
            owner = func.value
            owner_name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
            if owner_name != "tools" or not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            keywords = {k.arg for k in node.keywords if k.arg is not None}
            spreads = any(k.arg is None for k in node.keywords)  # **kwargs at the call site
            yield f"{path.relative_to(_AGENTS.parent).as_posix()}:{node.lineno}", node.args[0].value, keywords, spreads


def test_every_agent_tool_call_matches_the_tool():
    registry = register_all_tools(ToolRegistry())
    problems = []
    calls = list(_tool_calls())
    assert calls, "expected to find agent tool calls"
    for where, name, keywords, spreads in calls:
        tool = registry.get(name)
        if tool is None:
            problems.append(f"{where}: no tool named {name!r}")
            continue
        params = inspect.signature(tool.fn).parameters
        accepted = set(params)
        required = {n for n, p in params.items() if p.default is inspect.Parameter.empty
                    and p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)}
        unknown = keywords - accepted
        if unknown:
            problems.append(f"{where}: {name}() takes no {sorted(unknown)}; it accepts {sorted(accepted)}")
        missing = required - keywords
        if missing and not spreads:
            problems.append(f"{where}: {name}() needs {sorted(missing)}")
    assert problems == []


def test_introspect_args_split_the_connection_dict():
    from agents.tools import introspect_args

    args = introspect_args({"type": "mysql", "host": "db.example.com", "database": "shop"})
    assert args == {"connector_type": "mysql", "config": {"host": "db.example.com", "database": "shop"}}
    assert introspect_args({})["connector_type"] == "postgresql"
