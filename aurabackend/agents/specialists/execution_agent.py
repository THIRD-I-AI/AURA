from typing import Any
from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent
import datetime
import decimal

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
            
        con = ctx.metadata.get("duckdb_con")
        if not con or not sql:
            result.status = AgentStatus.FAILED
            result.error = "No database connection or SQL provided by upstream agents."
            return result
            
        try:
            result.add_step(action="execute_sql", input_summary=f"Executing Query: {sql[:150]}...")
            
            db_result = con.execute(sql)
            columns = [desc[0] for desc in db_result.description]
            rows = db_result.fetchall()
            records = [{col: self._serialize_value(val) for col, val in zip(columns, row)} for row in rows]
            
            result.status = AgentStatus.SUCCESS
            result.output = {
                "records": records,
                "columns": columns,
                "rows": [[self._serialize_value(cell) for cell in row] for row in rows],
                "sql": sql
            }
            result.add_step(action="sql_success", output_summary=f"Successfully returned {len(records)} rows.")
        except Exception as e:
            result.status = AgentStatus.FAILED
            result.error = f"Database execution error: {str(e)}"
            result.add_step(action="sql_error", output_summary=str(e), severity="error")
            
        return result
