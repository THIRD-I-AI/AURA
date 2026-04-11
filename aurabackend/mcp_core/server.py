from __future__ import annotations

from typing import Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import APIKeyHeader

from .message_models import (
    MCPHandshakeRequest,
    MCPHandshakeResponse,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationResult,
)

API_KEY_HEADER = APIKeyHeader(name="x-mcp-api-key", auto_error=False)


class MCPServer:
    """Reusable FastAPI router that implements the MCP control plane."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        protocol_version: str = "1.0",
    ) -> None:
        self._api_key = api_key
        self._protocol_version = protocol_version
        self._tools: Dict[str, ToolDescriptor] = {}
        self._invocation_handlers: Dict[str, Callable[[ToolInvocation], ToolInvocationResult]] = {}
        self.router = APIRouter(prefix="/mcp", tags=["mcp"])
        self._register_routes()

    def register_tool(
        self,
        descriptor: ToolDescriptor,
        handler: Callable[[ToolInvocation], ToolInvocationResult],
    ) -> None:
        if descriptor.tool_id in self._tools:
            raise ValueError(f"Tool {descriptor.tool_id} already registered")
        self._tools[descriptor.tool_id] = descriptor
        self._invocation_handlers[descriptor.tool_id] = handler

    def _register_routes(self) -> None:
        self.router.add_api_route(
            "/handshake",
            self._handshake_endpoint,
            methods=["POST"],
            response_model=MCPHandshakeResponse,
        )
        self.router.add_api_route(
            "/invoke",
            self._invoke_endpoint,
            methods=["POST"],
            response_model=ToolInvocationResult,
        )

    def _handshake_endpoint(
        self,
        request: MCPHandshakeRequest,
        provided_key: Optional[str] = Depends(API_KEY_HEADER),
    ) -> MCPHandshakeResponse:
        self._enforce_key(provided_key)
        granted = self._resolve_granted_tools(request)
        return MCPHandshakeResponse(
            session_id=request.session_id,
            protocol_version=self._protocol_version,
            accepted=True,
            granted_tools=granted,
        )

    def _invoke_endpoint(
        self,
        invocation: ToolInvocation,
        provided_key: Optional[str] = Depends(API_KEY_HEADER),
    ) -> ToolInvocationResult:
        self._enforce_key(provided_key)
        handler = self._invocation_handlers.get(invocation.tool_id)
        if not handler:
            raise HTTPException(status_code=404, detail="Tool not registered")
        return handler(invocation)

    def _enforce_key(self, provided_key: Optional[str]) -> None:
        if self._api_key and provided_key != self._api_key:
            raise HTTPException(status_code=401, detail="Invalid MCP key")

    def _resolve_granted_tools(self, request: MCPHandshakeRequest) -> list[ToolDescriptor]:
        if not request.requested_tools:
            return list(self._tools.values())

        granted: list[ToolDescriptor] = []
        for requested in request.requested_tools:
            existing = self._tools.get(requested.tool_id)
            if existing:
                granted.append(existing)
        return granted
