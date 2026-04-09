"""
AURA Insights Service - Data analysis and visualization insights
Auto-generates insights, charts, and narratives from query results
"""

import os
import sys
from typing import Dict, Any, List, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

# Add parent directory to path for imports

from shared.service_factory import create_service
from shared.logging_config import get_logger
from insights.engine import InsightsEngine

logger = get_logger("aura.insights")

app = create_service(
    name="Insights",
    service_tag="insights",
    description="Auto-generates insights and visualizations from query results",
)


# ==================== Models ====================

class AnalyzeRequest(BaseModel):
    """Request to analyze results"""
    query: str = Field(..., description="The SQL query that produced the results")
    results: List[Dict[str, Any]] = Field(..., description="Query result rows")
    column_profiles: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional column metadata and profiles"
    )


class AnalyzeResponse(BaseModel):
    """Response with insights"""
    insights: List[Dict[str, Any]] = Field(..., description="Generated insights")
    chart_suggestions: List[Dict[str, Any]] = Field(
        ...,
        description="Suggested chart types and configurations"
    )
    narrative: str = Field(..., description="Natural language summary")
    row_count: int = Field(..., description="Number of rows analyzed")
    column_count: int = Field(..., description="Number of columns")


class ChartSuggestionRequest(BaseModel):
    """Request chart suggestions"""
    columns: List[str] = Field(..., description="Available columns")
    data_sample: List[Dict[str, Any]] = Field(
        ...,
        description="Sample of data for analysis"
    )
    query: Optional[str] = Field(None, description="Original query context")


class ChartSuggestionResponse(BaseModel):
    """Suggested charts"""
    suggestions: List[Dict[str, Any]] = Field(
        ...,
        description="List of suggested chart types with configurations"
    )


# Health is provided by create_service()


# ==================== Insights API ====================

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_results(request: AnalyzeRequest):
    """Analyze query results and generate insights"""
    try:
        engine = InsightsEngine()
        
        # Generate analysis
        analysis = engine.analyze(
            request.query,
            request.results,
            request.column_profiles,
        )
        
        # Determine chart suggestions based on data
        chart_suggestions = _suggest_charts(
            columns=list(request.results[0].keys()) if request.results else [],
            data_sample=request.results[:10] if request.results else [],
            query=request.query,
        )
        
        return AnalyzeResponse(
            insights=analysis.get("insights", []),
            chart_suggestions=chart_suggestions,
            narrative=analysis.get("narrative", ""),
            row_count=len(request.results),
            column_count=len(request.results[0].keys()) if request.results else 0,
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}",
        )


@app.post("/chart-suggestions", response_model=ChartSuggestionResponse)
async def suggest_charts(request: ChartSuggestionRequest):
    """Suggest appropriate chart types for the data"""
    try:
        suggestions = _suggest_charts(
            columns=request.columns,
            data_sample=request.data_sample,
            query=request.query,
        )
        
        return ChartSuggestionResponse(suggestions=suggestions)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chart suggestion failed: {str(e)}",
        )


# ==================== Agent Tool Endpoints ====================

class RecommendIndexesRequest(BaseModel):
    """Request index recommendations"""
    table: str = Field(..., description="Table name to analyze")
    query_patterns: List[str] = Field(
        default_factory=list,
        description="Common query patterns run against this table",
    )


@app.post("/recommend-indexes")
async def recommend_indexes(request: RecommendIndexesRequest):
    """Recommend indexes for a table based on query patterns.

    Used by the agentic DE framework's optimization agent.
    Returns heuristic-based index suggestions.
    """
    suggestions: List[Dict[str, Any]] = []
    table = request.table
    patterns = request.query_patterns

    for i, pattern in enumerate(patterns):
        pattern_lower = pattern.lower()

        # Extract column hints from WHERE, JOIN, ORDER BY clauses
        recommended_cols: List[str] = []
        for keyword in ("where", "join", "order by", "group by"):
            idx = pattern_lower.find(keyword)
            if idx != -1:
                # Simple heuristic: grab tokens after the keyword
                fragment = pattern_lower[idx + len(keyword):].strip()
                # Take first word-like token as potential column
                token = fragment.split()[0].strip("(),;") if fragment.split() else None
                if token and token not in ("and", "or", "on", "by", "asc", "desc"):
                    recommended_cols.append(token)

        if recommended_cols:
            suggestions.append({
                "table": table,
                "columns": list(set(recommended_cols)),
                "index_type": "btree",
                "reason": f"Columns appear in filter/join/sort of pattern #{i + 1}",
                "pattern": pattern[:120],
            })

    # Default suggestion if no patterns provided
    if not suggestions:
        suggestions.append({
            "table": table,
            "columns": [],
            "index_type": "btree",
            "reason": "No query patterns provided — consider indexing primary key and common filter columns",
        })

    return {
        "table": table,
        "recommendations": suggestions,
        "count": len(suggestions),
    }


# ==================== Helper Functions ====================

def _suggest_charts(
    columns: List[str],
    data_sample: List[Dict[str, Any]],
    query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Suggest appropriate chart types based on column profiles and query intent."""
    suggestions: List[Dict[str, Any]] = []

    if not columns or not data_sample:
        return [{"type": "table", "title": "Data Table", "confidence": 1.0}]

    query_lower = (query or "").lower()

    # Classify columns
    time_columns = [
        c for c in columns
        if any(t in c.lower() for t in ["date", "time", "year", "month", "day", "week", "quarter"])
    ]

    numeric_columns: List[str] = []
    categorical_columns: List[str] = []
    for col in columns:
        val = data_sample[0].get(col) if data_sample else None
        if isinstance(val, (int, float)):
            numeric_columns.append(col)
        elif isinstance(val, str) and val:
            categorical_columns.append(col)

    row_count = len(data_sample)

    # 1. Time-series → line chart (highest confidence for time + numeric)
    if time_columns and numeric_columns:
        suggestions.append({
            "type": "line",
            "title": f"{numeric_columns[0]} Over Time",
            "xAxis": time_columns[0],
            "yAxis": numeric_columns[0],
            "series": numeric_columns[:3],
            "confidence": 0.95,
            "reason": "Time column detected with numeric metric",
        })

    # 2. Category + numeric → bar / horizontal bar
    if categorical_columns and numeric_columns:
        orient = "horizontal" if row_count > 10 else "vertical"
        suggestions.append({
            "type": "bar",
            "title": f"{numeric_columns[0]} by {categorical_columns[0]}",
            "xAxis": categorical_columns[0],
            "yAxis": numeric_columns[0],
            "orientation": orient,
            "confidence": 0.90,
            "reason": "Categorical grouping with numeric measure",
        })

    # 3. Single categorical with 2-8 unique values → pie chart
    if categorical_columns and numeric_columns and row_count <= 8:
        suggestions.append({
            "type": "pie",
            "title": f"Distribution of {numeric_columns[0]}",
            "dimension": categorical_columns[0],
            "measure": numeric_columns[0],
            "confidence": 0.80,
            "reason": "Small number of categories — good for proportional view",
        })

    # 4. Two numeric columns → scatter plot
    if len(numeric_columns) >= 2:
        suggestions.append({
            "type": "scatter",
            "title": f"{numeric_columns[0]} vs {numeric_columns[1]}",
            "xAxis": numeric_columns[0],
            "yAxis": numeric_columns[1],
            "confidence": 0.75,
            "reason": "Two numeric columns — scatter shows correlation",
        })

    # 5. Single numeric column → histogram (distribution)
    if len(numeric_columns) == 1 and not time_columns:
        suggestions.append({
            "type": "histogram",
            "title": f"Distribution of {numeric_columns[0]}",
            "column": numeric_columns[0],
            "bins": 20,
            "confidence": 0.85,
            "reason": "Single numeric column — histogram shows distribution",
        })

    # 6. Query-intent boosts
    if any(kw in query_lower for kw in ["trend", "over time", "monthly", "daily", "weekly"]):
        for s in suggestions:
            if s["type"] == "line":
                s["confidence"] = min(1.0, s["confidence"] + 0.05)

    if any(kw in query_lower for kw in ["top", "rank", "best", "worst", "largest", "smallest"]):
        for s in suggestions:
            if s["type"] == "bar":
                s["confidence"] = min(1.0, s["confidence"] + 0.05)
                s["sort"] = "desc"

    if any(kw in query_lower for kw in ["compare", "versus", "vs", "correlation"]):
        for s in suggestions:
            if s["type"] == "scatter":
                s["confidence"] = min(1.0, s["confidence"] + 0.10)

    # Always include table as fallback
    suggestions.append({"type": "table", "title": "Data Table", "confidence": 1.0})

    # Sort by confidence descending
    suggestions.sort(key=lambda s: s.get("confidence", 0), reverse=True)
    return suggestions


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("INSIGHTS_PORT", "8005")),
    )
