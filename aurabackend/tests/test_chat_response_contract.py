"""The frontend's type for a POST /chat answer matches what the backend sends.

Closes the class behind BUG-424 and BUG-425 from the backend side: a field or a
status the backend sends that the frontend's ``QueryResponse`` / ``ExecutionResult``
types do not know about is a field no screen will ever read. The frontend decides
what an answer means in one place (``services/chatResponse.ts``); this keeps the
type it reads honest.
"""
from __future__ import annotations

import re
from pathlib import Path

from api_gateway.routers.chat import ChatResponse, ExecutionResult

_API_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "services" / "api.ts"
_CHAT_PY = Path(__file__).resolve().parents[1] / "api_gateway" / "routers" / "chat.py"


def _ts_interface(name: str) -> str:
    text = _API_TS.read_text(encoding="utf-8")
    start = text.index(f"export interface {name} {{")
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1:j]
    raise AssertionError(f"unterminated interface {name}")


def _top_level_keys(body: str) -> set:
    keys, depth = set(), 0
    for line in body.splitlines():
        if depth == 0:
            m = re.match(r"\s*([a-z_]+)\??\s*:", line)
            if m:
                keys.add(m.group(1))
        depth += line.count("{") - line.count("}")
    return keys


def test_every_chat_response_field_is_in_the_frontend_type():
    missing = set(ChatResponse.model_fields) - _top_level_keys(_ts_interface("QueryResponse"))
    assert missing == set(), f"POST /chat sends {sorted(missing)}; frontend QueryResponse lacks them"


def test_every_execution_result_field_is_in_the_frontend_type():
    missing = set(ExecutionResult.model_fields) - _top_level_keys(_ts_interface("ExecutionResult"))
    assert missing == set(), f"execution_result carries {sorted(missing)}; frontend ExecutionResult lacks them"


def test_every_status_the_route_returns_is_one_the_frontend_knows():
    source = _CHAT_PY.read_text(encoding="utf-8")
    sent = set(re.findall(r'ChatResponse\(\s*status="([A-Za-z]+)"', source))
    assert sent, "expected literal statuses in the chat route"
    union = re.search(r"status:\s*([^;]+);", _ts_interface("QueryResponse")).group(1)
    known = set(re.findall(r"'([A-Za-z]+)'", union))
    assert sent - known == set(), f"the chat route returns {sorted(sent - known)}, unknown to the frontend type"
