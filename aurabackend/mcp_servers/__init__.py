"""
AURA MCP Servers
=================
Real Anthropic Model Context Protocol servers (stdio + SSE) that expose
local DuckDB analytics and the distributed metadata layer (Postgres-wire
compatible: CockroachDB / YugabyteDB / Postgres).

Run standalone:
    python -m mcp_servers.aura_mcp_server               # stdio (Claude Code)
    python -m mcp_servers.aura_mcp_server --http 8765   # SSE (in-cluster)
"""
