import asyncio
import datetime
import decimal
import os
from typing import Any, cast

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent
from agents.params import ExecutionAgentParams


def _max_result_rows() -> int:
    """Row cap for chat SQL, the same AURA_QUERY_MAX_ROWS that /execute applies (BUG-228)."""
    try:
        return max(1, int(os.getenv("AURA_QUERY_MAX_ROWS", "10000")))
    except ValueError:
        return 10000


class ExecutionAgent(BaseAgent):
    """
    Takes an upstream generated SQL query from the SQLGeneratorAgent and securely executes it
    against the provided DuckDB local connection, returning serialized rows and columns.
    """

    name = "ExecutionAgent"
    description = "Executes generated SQL securely against the database."

    def _serialize_value(self, val: Any) -> Any:
        if isinstance(val, decimal.Decimal): return float(val)
        if isinstance(val, (datetime.datetime,)): return val.isoformat()
        if hasattr(val, 'isoformat'): return val.isoformat()
        return val

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        sql = ""
        # Search upstream results for the generated SQL string
        for dep_result in ctx.upstream_results.values():
            if isinstance(dep_result, str):
                sql = dep_result
            elif isinstance(dep_result, dict) and "sql" in dep_result:
                sql = dep_result["sql"]

        # Basic parsing if wrapped in markdown
        if sql.startswith("```sql"):
            sql = sql.replace("```sql", "").replace("```", "").strip()

        params = cast(ExecutionAgentParams, ctx.metadata or {})
        con = params.get("duckdb_con")
        if not con or not sql:
            result.status = AgentStatus.FAILED
            result.error = "No database connection or SQL provided by upstream agents."
            return result

        try:
            result.add_step(action="execute_sql", input_summary=f"Executing Query: {sql[:150]}...")

            # BUG-359: this was fetchall(). LLM SQL has no enforced LIMIT (the prompt
            # tells the model to omit it when the user asks for all rows), so one chat
            # question over a large upload pulled every row into the single worker's
            # memory -- three copies, counting records and rows -- and into one response.
            cap = _max_result_rows()

            def _run_sql() -> tuple[list[str], list[tuple]]:
                cur = con.execute(sql)
                cols = [desc[0] for desc in cur.description]
                return cols, cur.fetchmany(cap + 1)

            columns, rows = await asyncio.to_thread(_run_sql)
            truncated = len(rows) > cap
            rows = rows[:cap]
            records = [{col: self._serialize_value(val) for col, val in zip(columns, row)} for row in rows]

            result.status = AgentStatus.SUCCESS
            result.output = {
                "records": records,
                "columns": columns,
                "rows": [[self._serialize_value(cell) for cell in row] for row in rows],
                "sql": sql,
                "truncated": truncated,
            }
            result.add_step(action="sql_success", output_summary=f"Successfully returned {len(records)} rows.")
        except Exception as e:
            result.status = AgentStatus.FAILED
            result.error = f"Database execution error: {str(e)}"
            result.add_step(action="sql_error", output_summary=str(e), severity="error")

        return result
