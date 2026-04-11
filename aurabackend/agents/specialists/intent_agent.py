from typing import Any

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent
from shared.llm_provider import get_llm


class IntentAgent(BaseAgent):
    """
    Analyzes user input to determine if it requires a SQL query or if it is purely conversational.
    Used as an early exit gateway so the planner doesn't run full database analysis for greetings.
    """

    name = "IntentAgent"
    description = "Classifies user intent as 'conversational' or 'sql'."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        try:
            llm = get_llm()
            # Stringify context lightly to avoid overly massive prompt if many tables
            schema_keys = list(ctx.schema_context.keys()) if ctx.schema_context else []

            intent_prompt = f"""You are AURA, an intelligent data assistant. Analyze the user's message and determine if it requires executing a SQL query against the database, or if it is a general conversational message (like a greeting, thanking, asking for help, or asking about the metadata/columns available).

Available Tables Context:
{schema_keys}

User's message: "{ctx.user_prompt}"

Respond STRICTLY with a JSON object in this format (no markdown code blocks, just raw JSON):
{{"intent": "sql" or "conversation", "message": "If conversation, put your helpful natural language response here. If sql, leave blank."}}
"""
            result.add_step(action="classify_intent", input_summary=f"User prompt: {ctx.user_prompt}")
            classifier_result = llm.generate_json(intent_prompt)
            result.output = classifier_result or {"intent": "sql"}
            result.status = AgentStatus.SUCCESS
            result.add_step(action="intent_classified", output_summary=result.output.get("intent", "sql"))
        except Exception as e:
            result.error = str(e)
            result.status = AgentStatus.FAILED
            result.add_step(action="intent_error", output_summary=str(e), severity="error")
        return result
