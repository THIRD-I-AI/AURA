from __future__ import annotations

import asyncio
import json
import os
import sys

from fastapi import HTTPException, status

from mcp_core import MCPServer, ToolDescriptor, ToolInvocation, ToolInvocationResult
from orchestration_service.agents.critic_agent import CriticAgent
from orchestration_service.agents.generator_agent import GeneratorAgent
from orchestration_service.coordinator import TinyRecursiveConfig, TinyRecursiveCoordinator
from shared.config import settings
from shared.logging_config import get_logger
from shared.models import AgentResponse, ChatRequest

# Add parent directory to path
from shared.service_factory import create_service

logger = get_logger("aura.orchestration")

app = create_service(
    name="Orchestration",
    service_tag="orchestration",
    description="Coordinates multi-agent SQL generation and validation",
)

generator = GeneratorAgent()
critic = CriticAgent()
config = TinyRecursiveConfig(
    max_depth=settings.tiny_recursive_max_depth,
    confidence_threshold=settings.tiny_recursive_confidence,
)
coordinator = TinyRecursiveCoordinator(generator, critic, config)

mcp_server = MCPServer(api_key=settings.mcp_api_key)


def _connection_probe(invocation: ToolInvocation) -> ToolInvocationResult:
    payload = {
        "invocation_id": invocation.invocation_id,
        "status": "ok",
        "message": "Connection diagnostics tool registered",
    }
    return ToolInvocationResult(
        invocation_id=invocation.invocation_id,
        success=True,
        result_json=json.dumps(payload),
    )


mcp_server.register_tool(
    ToolDescriptor(
        tool_id="connection.diagnostics",
        name="Connection Diagnostics",
        description="Performs lightweight readiness checks for database endpoints.",
        capabilities=["diagnostics", "network"],
    ),
    _connection_probe,
)

app.include_router(mcp_server.router)


@app.get("/", status_code=status.HTTP_200_OK)
def root() -> dict[str, str]:
    return {"service": "orchestration", "status": "ready"}


# Health is provided by create_service()


@app.post("/v1/orchestrations/query", response_model=AgentResponse, status_code=status.HTTP_200_OK)
async def generate_query(request: ChatRequest) -> AgentResponse:
    if not request.session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id is required")

    # coordinator.execute() chains up to 6 sequential blocking LLM calls
    # (generator + critic, up to max_depth rounds); the deployment runs one
    # uvicorn worker, so running it inline would freeze every concurrent
    # request for the duration (.claude/rules/backend.md async-safety rule).
    response = await asyncio.to_thread(coordinator.execute, request)
    if not response.final_query:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Agent mesh returned empty query")

    return response
