from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, cast

from pydantic import BaseModel, Field


def _empty_tool_descriptors() -> List["ToolDescriptor"]:
    return cast(List[ToolDescriptor], [])


class ToolDescriptor(BaseModel):
    tool_id: str
    name: str
    description: str
    capabilities: List[str] = Field(default_factory=list)
    metadata: Dict[str, str] = Field(default_factory=dict)


class MCPHandshakeRequest(BaseModel):
    session_id: str
    protocol_version: str = "1.0"
    requested_tools: List[ToolDescriptor] = Field(default_factory=_empty_tool_descriptors)


class MCPHandshakeResponse(BaseModel):
    session_id: str
    protocol_version: str
    accepted: bool
    reason: Optional[str] = None
    granted_tools: List[ToolDescriptor] = Field(default_factory=_empty_tool_descriptors)


class ToolInvocation(BaseModel):
    invocation_id: str
    tool_id: str
    session_id: str
    payload_json: str


class ToolInvocationResult(BaseModel):
    invocation_id: str
    success: bool
    result_json: Optional[str] = None
    error_message: Optional[str] = None


class AgentEnvelope(BaseModel):
    session_id: str
    agent_id: str
    message_type: str
    payload: str
    headers: Dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
