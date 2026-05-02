"""
Data Agnostic Researcher (DAR)
================================
Headless background service that proactively explores the shared
DuckDB analytics lake (UASR_DUCKDB_PATH). On each tick it picks one
table, runs a 6-node LangGraph DAG (introspect → profile → formulate →
execute → score → persist), and stores every finding in the metadata
DB's ``dar_insights`` table.

No human prompt enters this loop — DAR formulates its own questions
from schema + distribution data.

Architecture mirrors the UASR self-healing loop: env-gated daemon
opt-in (``AURA_DAR_ENABLED``), shared singletons across HTTP +
background paths, graceful degradation when the LLM is unreachable.
"""
