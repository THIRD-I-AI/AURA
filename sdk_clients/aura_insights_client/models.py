"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: 22549c3cfed13f5e
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class AnalyzeRequest(BaseModel):
    """Request to analyze results"""
    query: str
    results: List[Dict[str, Any]]
    column_profiles: Optional[Dict[str, Any]] = None


class AnalyzeResponse(BaseModel):
    """Response with insights"""
    chart_suggestions: List[Dict[str, Any]]
    column_count: int
    insights: List[Dict[str, Any]]
    narrative: str
    row_count: int


class ChartSuggestionRequest(BaseModel):
    """Request chart suggestions"""
    columns: List[str]
    data_sample: List[Dict[str, Any]]
    query: Optional[str] = None


class ChartSuggestionResponse(BaseModel):
    """Suggested charts"""
    suggestions: List[Dict[str, Any]]


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class RecommendIndexesRequest(BaseModel):
    """Request index recommendations"""
    table: str
    query_patterns: Optional[List[str]] = None


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str

