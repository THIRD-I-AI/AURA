from typing import Any

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent
from shared.llm_provider import get_llm


class AnalysisAgent(BaseAgent):
    """
    Analyzes raw data results (from SQL execution) and synthesizes a concise, natural language conclusion.
    Provides standard agent telemetry and step logging.
    """
    
    name = "AnalysisAgent"
    description = "Analyzes tabular data results and synthesizes a targeted narrative conclusion."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        upstream_data = None
        for dep_result in ctx.upstream_results.values():
            if isinstance(dep_result, dict) and "records" in dep_result:
                upstream_data = dep_result
                break

        records = upstream_data["records"] if upstream_data else []
        
        if not records:
            result.status = AgentStatus.SUCCESS
            result.output["conclusion"] = None
            return result
        
        try:
            llm = get_llm()
            limited_records = records[:20]
            conclusion_prompt = (
                f"User asked: '{ctx.user_prompt}'\n"
                f"Data result (first 20 rows):\n{limited_records}\n"
                "Provide a brief, conclusive answer to the user's question based strictly on this data. "
                "Do not show the SQL. Keep it to 1-3 sentences in a friendly, helpful tone."
            )
            
            print(f"=== [ANALYSIS AGENT PROMPT] ===\n{conclusion_prompt}\n================================")
            
            result.add_step(
                action="analyze_data",
                input_summary=f"Analyzing {len(limited_records)} rows of data."
            )
            
            # TODO: Future enhancement: Equip agent with tools to loop, chart, or statistically analyze data.
            conclusion = llm.generate(conclusion_prompt)
            print(f"=== [ANALYSIS AGENT OUTPUT] ===\n{conclusion}\n================================")
            
            result.add_step(
                action="synthesize_conclusion",
                output_summary=f"Generated conclusion length: {len(conclusion) if conclusion else 0} chars."
            )
            
            result.status = AgentStatus.SUCCESS
            result.output["conclusion"] = conclusion

        except Exception as e:
            result.status = AgentStatus.FAILED
            result.error = f"Analysis failed: {str(e)}"
            result.add_step(
                action="analysis_error",
                output_summary=str(e)
            )

        return result
