"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: 45d13ef4961b66e7
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class AgentAsyncResponse(BaseModel):
    session_id: str
    topic: str


class AgentExecuteRequest(BaseModel):
    prompt: str
    files: Optional[List[str]] = None
    connection: Optional[Dict[str, Any]] = None
    schema_context: Optional[Dict[str, Any]] = None
    execute_sql: Optional[bool] = None


class AgentExecuteResponse(BaseModel):
    success: bool
    summary: str
    duration_ms: float
    tasks: Dict[str, Any]
    skipped: List[str]


class AgentPlanResponse(BaseModel):
    plan_id: str
    summary: str
    tasks: List[Dict[str, Any]]


class Body_analyze_results_api_v1_analyze_results_post(BaseModel):
    results: List[Dict[str, Any]]
    column_profiles: Optional[Dict[str, Any]] = None


class Body_upload_universal_api_v1_upload_post(BaseModel):
    file: Optional[bytes] = None
    upload_file: Optional[bytes] = None


class ChatRequest(BaseModel):
    message: str
    context: Optional[str] = None
    session_id: Optional[str] = None
    uploaded_file: Optional[str] = None
    columns: Optional[List[str]] = None
    auto_execute: Optional[bool] = None


class ConnectionCreateRequest(BaseModel):
    name: str
    type: str
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    ssl: Optional[bool] = None
    extra: Optional[Dict[str, Any]] = None


class ConnectorTableListResponse(BaseModel):
    connector_id: str
    tables: List[str]
    total_count: int


class CreateStreamPipelineRequest(BaseModel):
    name: str
    source: "StreamSource"
    description: Optional[str] = None
    event_time_field: Optional[str] = None
    watermark_delay_seconds: Optional[int] = None
    window: Optional["WindowConfig"] = None
    transforms: Optional[List["StreamTransform"]] = None
    sinks: Optional[List["StreamSink"]] = None
    checkpoint_interval_seconds: Optional[int] = None
    tags: Optional[List[str]] = None


class ETLNaturalLanguageRequest(BaseModel):
    source_file: str
    instruction: str
    destination_format: Optional[str] = None


class ETLPipelineRequest(BaseModel):
    source_file: str
    name: Optional[str] = None
    destination_format: Optional[str] = None
    destination_filename: Optional[str] = None
    transforms: Optional[List["ETLTransformStep"]] = None
    preview_only: Optional[bool] = None


class ETLTransformStep(BaseModel):
    type: str
    id: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class ExecuteQueryRequest(BaseModel):
    query: str
    connector_type: str
    connector_config: Dict[str, Any]
    dry_run: Optional[bool] = None


class ExecuteQueryResponse(BaseModel):
    success: bool
    data: Optional[List[Dict[str, Any]]]
    rows: int
    columns: List[str]
    insights: Optional[Dict[str, Any]]
    error: Optional[str]
    execution_time_ms: float


class FeedbackRequest(BaseModel):
    session_id: str
    agent_name: str
    task_type: str
    user_prompt: str
    agent_output: Optional[Dict[str, Any]] = None
    success: Optional[bool] = None
    duration_ms: Optional[float] = None
    user_rating: Optional[int] = None
    correction: Optional[str] = None


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class HookCreateRequest(BaseModel):
    slug: str
    kind: str
    target: str
    secret: Optional[str] = None
    description: Optional[str] = None
    pass_payload_as: Optional[str] = None


class HookUpdateRequest(BaseModel):
    slug: Optional[str] = None
    kind: Optional[str] = None
    target: Optional[str] = None
    secret: Optional[str] = None
    description: Optional[str] = None
    pass_payload_as: Optional[str] = None
    active: Optional[bool] = None


class LateDataPolicy(BaseModel):
    """How to handle events that arrive after the watermark."""
    pass


class PatternSearchRequest(BaseModel):
    intent: str
    pattern_type: Optional[str] = None
    limit: Optional[int] = None


class PipelineExecuteRequest(BaseModel):
    pipeline: Dict[str, Any]
    preview_only: Optional[bool] = None


class PipelineGenerateRequest(BaseModel):
    prompt: str
    source_file: Optional[str] = None
    include_schema: Optional[bool] = None


class PipelineSaveRequest(BaseModel):
    pipeline: Dict[str, Any]


class ProfileTableRequest(BaseModel):
    connector_type: str
    connector_config: Dict[str, Any]
    table_name: str


class ProposalUpdateRequest(BaseModel):
    status: str
    test_results: Optional[Dict[str, Any]] = None
    confidence_score: Optional[float] = None


class QueryRequest(BaseModel):
    session_id: str
    prompt: str
    context: Optional[str] = None


class SemanticFieldPayload(BaseModel):
    name: str
    id: Optional[str] = None
    field_type: Optional[str] = None
    data_type: Optional[str] = None
    expression: Optional[str] = None
    description: Optional[str] = None
    aggregation: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SemanticModelPayload(BaseModel):
    name: str
    id: Optional[str] = None
    description: Optional[str] = None
    source: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    fields: Optional[List["SemanticFieldPayload"]] = None


class StreamSink(BaseModel):
    """Defines where processed data is emitted."""
    type: "StreamSinkType"
    config: Optional[Dict[str, Any]] = None


class StreamSinkType(BaseModel):
    """Where processed streaming data goes."""
    pass


class StreamSource(BaseModel):
    """Defines where streaming data originates."""
    type: "StreamSourceType"
    config: Optional[Dict[str, Any]] = None


class StreamSourceType(BaseModel):
    """Where streaming data originates."""
    pass


class StreamTransform(BaseModel):
    """One processing operation in the streaming pipeline."""
    type: "TransformType"
    id: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class TokenRequest(BaseModel):
    """
    Minimal login: user id + optional metadata.

    In production this should validate credentials (password, OAuth code,
    etc.).  For now it issues a token for the given ``user_id`` so the
    rest of the JWT pipeline can be exercised end-to-end.
    """
    user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Optional[str] = None


class TransformType(BaseModel):
    """Processing operations available in streaming transforms."""
    pass


class UpdateStreamPipelineRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source: Optional["StreamSource"] = None
    event_time_field: Optional[str] = None
    watermark_delay_seconds: Optional[int] = None
    window: Optional["WindowConfig"] = None
    transforms: Optional[List["StreamTransform"]] = None
    sinks: Optional[List["StreamSink"]] = None
    checkpoint_interval_seconds: Optional[int] = None
    tags: Optional[List[str]] = None


class UserInfo(BaseModel):
    sub: str
    email: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None


class ValidateQueryRequest(BaseModel):
    query: str
    dry_run_mode: Optional[bool] = None
    max_rows: Optional[int] = None


class ValidateQueryResponse(BaseModel):
    is_valid: bool
    risk_level: str
    warnings: List[str]
    errors: List[str]
    suggested_query: Optional[str]
    row_count_estimate: int
    estimated_execution_ms: Optional[float]


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str


class WebhookCreateRequest(BaseModel):
    url: str
    events: Optional[List[str]] = None
    secret: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    retries: Optional[int] = None
    description: Optional[str] = None


class WebhookUpdateRequest(BaseModel):
    url: Optional[str] = None
    events: Optional[List[str]] = None
    secret: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    retries: Optional[int] = None
    active: Optional[bool] = None
    description: Optional[str] = None


class WindowConfig(BaseModel):
    """Configuration for temporal windowing."""
    type: Optional["WindowType"] = None
    size_seconds: Optional[int] = None
    slide_seconds: Optional[int] = None
    gap_seconds: Optional[int] = None
    late_data_policy: Optional["LateDataPolicy"] = None
    allowed_lateness_seconds: Optional[int] = None


class WindowType(BaseModel):
    """Temporal window strategies for stream aggregation."""
    pass


class _ChatExecuteRequest(BaseModel):
    sql: str
    connection_id: Optional[str] = None

