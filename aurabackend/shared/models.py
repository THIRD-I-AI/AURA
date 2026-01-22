from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    session_id: str
    prompt: str
    context: Optional[str] = Field(
        default="Schema: sales_table(product_name, total_revenue, sale_date)",
        description="Database schema or other context for the query."
    )

class PlanStep(BaseModel):
    step: str
    task: Optional[str] = None
    chart_type: Optional[str] = None

class ExecutionJob(BaseModel):
    job_id: str
    sql: str
    connection_id: Optional[str] = Field(
        default=None,
        description="Identifier of the database connection to execute against."
    )
    limit: int = Field(
        default=1000,
        ge=1,
        le=10_000,
        description="Maximum number of rows to return from the query."
    )
    approved: bool = False
    status: str = "pending"
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None

class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    chart_spec: Optional[dict[str, Any]] = None

class ValidationResult(BaseModel):
    is_valid: bool = Field(description="Whether the generated code is valid.")
    reason: str = Field(description="Explanation for why the code is valid or not.")
    rework_suggestion: Optional[str] = Field(
        default=None,
        description="Specific feedback for the Generator Agent if rework is needed."
    )

class AgentResponse(BaseModel):
    status: str
    final_query: Optional[str] = None
    error_message: Optional[str] = None
    details: Optional[str] = None
    confidence: Optional[float] = Field(default=None, description="Confidence score assigned by critic agent.")
    job_id: Optional[str] = None
