from __future__ import annotations

import json
import os
import sys

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import AgentResponse, ChatRequest
from orchestration_service.agents.critic_agent import CriticAgent
from orchestration_service.agents.generator_agent import GeneratorAgent
from orchestration_service.coordinator import TinyRecursiveConfig, TinyRecursiveCoordinator
from mcp_core import MCPServer, ToolDescriptor, ToolInvocation, ToolInvocationResult

load_dotenv()

app = FastAPI(
    title="AURA Orchestration Service",
    description="Coordinates multi-agent SQL generation and validation",
)

allowed_origins = os.getenv("ORCHESTRATION_ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

generator = GeneratorAgent()
critic = CriticAgent()
config = TinyRecursiveConfig(
    max_depth=int(os.getenv("TINY_RECURSIVE_MAX_DEPTH", "3")),
    confidence_threshold=float(os.getenv("TINY_RECURSIVE_CONFIDENCE", "0.8")),
)
coordinator = TinyRecursiveCoordinator(generator, critic, config)

mcp_server = MCPServer(api_key=os.getenv("MCP_API_KEY"))


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


@app.get("/health", status_code=status.HTTP_200_OK)
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/v1/orchestrations/query", response_model=AgentResponse, status_code=status.HTTP_200_OK)
async def generate_query(request: ChatRequest) -> AgentResponse:
    if not request.session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id is required")

    response = coordinator.execute(request)
    if not response.final_query:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Agent mesh returned empty query")

    return response
