"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: f769f3228944a322
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class AgentResponse(BaseModel):
    status: str
    confidence: Optional[float] = None
    details: Optional[str] = None
    error_message: Optional[str] = None
    final_query: Optional[str] = None
    job_id: Optional[str] = None


class ChatRequest(BaseModel):
    prompt: str
    session_id: str
    context: Optional[str] = None


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class MCPHandshakeRequest(BaseModel):
    session_id: str
    protocol_version: Optional[str] = None
    requested_tools: Optional[List["ToolDescriptor"]] = None


class MCPHandshakeResponse(BaseModel):
    accepted: bool
    protocol_version: str
    session_id: str
    granted_tools: Optional[List["ToolDescriptor"]] = None
    reason: Optional[str] = None


class ToolDescriptor(BaseModel):
    description: str
    name: str
    tool_id: str
    capabilities: Optional[List[str]] = None
    metadata: Optional[Dict[str, str]] = None


class ToolInvocation(BaseModel):
    invocation_id: str
    payload_json: str
    session_id: str
    tool_id: str


class ToolInvocationResult(BaseModel):
    invocation_id: str
    success: bool
    error_message: Optional[str] = None
    result_json: Optional[str] = None


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str
    ctx: Optional[Dict[str, Any]] = None
    input: Optional[Any] = None

