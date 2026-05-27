"""
Sprint S31b — MCP core tests.

Tier A (pure Python, no optional deps).

Covers:
  * message_models: ToolDescriptor, MCPHandshakeRequest/Response,
    ToolInvocation, ToolInvocationResult, AgentEnvelope
  * MCPServer: register_tool, duplicate rejection, handshake
    (all tools, filtered, with/without API key), invoke (success,
    missing tool, bad key)
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_core.message_models import (
    AgentEnvelope,
    MCPHandshakeRequest,
    MCPHandshakeResponse,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationResult,
)
from mcp_core.server import MCPServer

# ── message_models schema tests ───────────────────────────────────

class TestToolDescriptor:
    def test_basic(self):
        td = ToolDescriptor(tool_id="t1", name="echo", description="Echoes input")
        assert td.tool_id == "t1"
        assert td.capabilities == []
        assert td.metadata == {}

    def test_full(self):
        td = ToolDescriptor(
            tool_id="t2", name="search", description="Search docs",
            capabilities=["read"], metadata={"version": "1.0"},
        )
        assert td.capabilities == ["read"]
        assert td.metadata["version"] == "1.0"


class TestMCPHandshakeRequest:
    def test_defaults(self):
        req = MCPHandshakeRequest(session_id="s1")
        assert req.protocol_version == "1.0"
        assert req.requested_tools == []

    def test_with_tools(self):
        tool = ToolDescriptor(tool_id="t1", name="x", description="y")
        req = MCPHandshakeRequest(session_id="s1", requested_tools=[tool])
        assert len(req.requested_tools) == 1


class TestMCPHandshakeResponse:
    def test_accepted(self):
        resp = MCPHandshakeResponse(
            session_id="s1", protocol_version="1.0", accepted=True,
        )
        assert resp.accepted is True
        assert resp.reason is None
        assert resp.granted_tools == []


class TestToolInvocation:
    def test_fields(self):
        inv = ToolInvocation(
            invocation_id="inv1", tool_id="t1",
            session_id="s1", payload_json='{"key": "value"}',
        )
        assert inv.payload_json == '{"key": "value"}'


class TestToolInvocationResult:
    def test_success(self):
        result = ToolInvocationResult(
            invocation_id="inv1", success=True, result_json='{"out": 42}',
        )
        assert result.success is True
        assert result.error_message is None

    def test_failure(self):
        result = ToolInvocationResult(
            invocation_id="inv1", success=False, error_message="boom",
        )
        assert result.success is False
        assert result.result_json is None


class TestAgentEnvelope:
    def test_fields(self):
        env = AgentEnvelope(
            session_id="s1", agent_id="a1",
            message_type="request", payload="{}",
        )
        assert env.headers == {}
        assert env.timestamp is not None


# ── MCPServer tests ───────────────────────────────────────────────

def _make_app(api_key=None):
    server = MCPServer(api_key=api_key)
    tool = ToolDescriptor(tool_id="echo", name="echo", description="Echo tool")

    def echo_handler(invocation: ToolInvocation) -> ToolInvocationResult:
        return ToolInvocationResult(
            invocation_id=invocation.invocation_id,
            success=True,
            result_json=invocation.payload_json,
        )

    server.register_tool(tool, echo_handler)
    app = FastAPI()
    app.include_router(server.router)
    return app, server


class TestMCPServerRegistration:
    def test_register_tool(self):
        _, server = _make_app()
        assert "echo" in server._tools
        assert "echo" in server._invocation_handlers

    def test_duplicate_registration_raises(self):
        _, server = _make_app()
        dup = ToolDescriptor(tool_id="echo", name="echo2", description="dup")
        with pytest.raises(ValueError, match="already registered"):
            server.register_tool(dup, lambda inv: None)


class TestMCPServerHandshake:
    def test_handshake_returns_all_tools(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/mcp/handshake", json={
            "session_id": "s1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] is True
        assert len(data["granted_tools"]) == 1
        assert data["granted_tools"][0]["tool_id"] == "echo"

    def test_handshake_filters_requested_tools(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/mcp/handshake", json={
            "session_id": "s1",
            "requested_tools": [{"tool_id": "nonexistent", "name": "x", "description": "y"}],
        })
        assert resp.status_code == 200
        assert resp.json()["granted_tools"] == []

    def test_handshake_with_matching_filter(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/mcp/handshake", json={
            "session_id": "s1",
            "requested_tools": [{"tool_id": "echo", "name": "echo", "description": "Echo"}],
        })
        assert resp.status_code == 200
        assert len(resp.json()["granted_tools"]) == 1


class TestMCPServerInvoke:
    def test_invoke_success(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/mcp/invoke", json={
            "invocation_id": "inv1",
            "tool_id": "echo",
            "session_id": "s1",
            "payload_json": '{"msg": "hello"}',
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result_json"] == '{"msg": "hello"}'

    def test_invoke_missing_tool(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/mcp/invoke", json={
            "invocation_id": "inv1",
            "tool_id": "nonexistent",
            "session_id": "s1",
            "payload_json": "{}",
        })
        assert resp.status_code == 404


class TestMCPServerApiKey:
    def test_handshake_rejected_without_key(self):
        app, _ = _make_app(api_key="secret-key")
        client = TestClient(app)
        resp = client.post("/mcp/handshake", json={"session_id": "s1"})
        assert resp.status_code == 401

    def test_handshake_accepted_with_correct_key(self):
        app, _ = _make_app(api_key="secret-key")
        client = TestClient(app)
        resp = client.post(
            "/mcp/handshake",
            json={"session_id": "s1"},
            headers={"x-mcp-api-key": "secret-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True

    def test_invoke_rejected_with_wrong_key(self):
        app, _ = _make_app(api_key="secret-key")
        client = TestClient(app)
        resp = client.post(
            "/mcp/invoke",
            json={
                "invocation_id": "inv1", "tool_id": "echo",
                "session_id": "s1", "payload_json": "{}",
            },
            headers={"x-mcp-api-key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_no_key_configured_allows_all(self):
        app, _ = _make_app(api_key=None)
        client = TestClient(app)
        resp = client.post("/mcp/handshake", json={"session_id": "s1"})
        assert resp.status_code == 200
