"""
AURA Insights Service - Data analysis and visualization insights
Auto-generates insights, charts, and narratives from query results
"""

import os
import sys
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insights.engine import InsightsEngine

app = FastAPI(
    title="AURA Insights Service",
    description="Auto-generates insights and visualizations from query results",
)

# CORS Configuration
_cors_origins = os.getenv(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


# ==================== Health ====================

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "insights",
        "version": "1.0.0",
    }


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
    """Suggest appropriate chart types based on data"""
    suggestions = []
    
    if not columns or not data_sample:
        return suggestions
    
    query_lower = (query or "").lower()
    
    # Check for time-based data
    time_columns = [c for c in columns if any(
        t in c.lower() for t in ["date", "time", "year", "month", "day"]
    )]
    
    # Check for numeric columns
    numeric_columns = []
    for col in columns:
        try:
            if data_sample and col in data_sample[0]:
                val = data_sample[0][col]
                if isinstance(val, (int, float)):
                    numeric_columns.append(col)
        except:
            pass
    
    # Line chart for time series
    if time_columns and numeric_columns:
        suggestions.append({
            "type": "line",
            "title": "Time Series",
            "xAxis": time_columns[0],
            "yAxis": numeric_columns[0],
            "confidence": 0.95,
        })
    
    # Bar chart for categorical with aggregates
    if len(columns) >= 2 and "top" in query_lower:
        suggestions.append({
            "type": "bar",
            "title": "Top N Analysis",
            "xAxis": columns[0],
            "yAxis": numeric_columns[0] if numeric_columns else columns[1],
            "confidence": 0.90,
        })
    
    # Table as fallback
    suggestions.append({
        "type": "table",
        "title": "Data Table",
        "confidence": 1.0,
    })
    
    return suggestions


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
