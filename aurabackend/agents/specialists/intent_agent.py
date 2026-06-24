from typing import Any

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent


class IntentAgent(BaseAgent):
    """
    Analyzes user input to determine if it requires a SQL query or if it is purely conversational.
    Used as an early exit gateway so the planner doesn't run full database analysis for greetings.
    """

    name = "IntentAgent"
    description = "Classifies user intent as 'sql', 'pipeline', or 'conversation'."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        try:
            # Stringify context lightly to avoid overly massive prompt if many tables
            schema_keys = list(ctx.schema_context.keys()) if ctx.schema_context else []

            intent_prompt = f"""You are AURA, an intelligent data assistant and command center. Classify the user's message into EXACTLY ONE intent:

- "sql": a question about their data that is answered by querying it
  (e.g. "top products by revenue", "how many customers", "sales by month").
- "pipeline": a request to CREATE or BUILD an ETL / data pipeline / transformation
  workflow (e.g. "create a pipeline that loads orders and filters by region",
  "build an ETL job to clean the customer file", "ingest X then dedupe and
  aggregate"). Verbs like create / build / make a pipeline, ETL, ingest,
  transform, or workflow signal this — it is an ACTION, not a question.
- "audit": a request to AUDIT / forensically check a dataset for anomalies,
  fraud, irregularities, Benford's-law deviation, duplicates, round-dollar
  amounts, or to produce an audit certificate (e.g. "audit the salesorder
  data", "check the invoices for anomalies", "run a forensic audit on
  payments"). Verbs like audit / forensic / check for fraud-or-anomalies
  signal this — it is an ACTION that runs the auditor, not a question.
- "conversation": greetings, thanks, asking for help, or asking what
  tables/columns are available — anything that does NOT query the data, build
  a pipeline, or run an audit.

Available Tables Context:
{schema_keys}

User's message: "{ctx.user_prompt}"

Respond STRICTLY with a JSON object (no markdown code blocks, just raw JSON):
{{"intent": "sql" | "pipeline" | "audit" | "conversation", "message": "If conversation, put your helpful natural language response here; otherwise leave blank."}}
"""
            result.add_step(action="classify_intent", input_summary=f"User prompt: {ctx.user_prompt}")
            classifier_result = self.llm.generate_json(intent_prompt)
            result.output = classifier_result or {"intent": "sql"}
            result.status = AgentStatus.SUCCESS
            result.add_step(action="intent_classified", output_summary=result.output.get("intent", "sql"))
        except Exception as e:
            result.error = str(e)
            result.status = AgentStatus.FAILED
            result.add_step(action="intent_error", output_summary=str(e), severity="error")
        return result
