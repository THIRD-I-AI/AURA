"""
Agent Memory
=============
Short-term (conversation) and long-term (persistent) memory for agents.
Keeps track of what was done, what was learned, and what failed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MemoryEntry:
    role: str                       # "user", "agent", "tool", "system"
    content: str
    agent_name: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


class AgentMemory:
    """
    Provides short-term context to agents.

    Every agent execution appends entries.  The planner agent reads
    the full memory to decide what to do next.
    """

    def __init__(self, max_entries: int = 500) -> None:
        self._entries: List[MemoryEntry] = []
        self._max = max_entries

        # Learned facts (persisted across turns)
        self._facts: Dict[str, Any] = {}

        # Schema cache (populated by SchemaArchitectAgent)
        self._schema_cache: Dict[str, Any] = {}

    # ── append / read ───────────────────────────────────────────────

    def add(self, role: str, content: str, *,
            agent_name: str = "", metadata: Optional[Dict[str, Any]] = None,
            tags: Optional[List[str]] = None) -> None:
        entry = MemoryEntry(
            role=role,
            content=content,
            agent_name=agent_name,
            metadata=metadata or {},
            tags=tags or [],
        )
        self._entries.append(entry)
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]

    def entries(self, agent_name: Optional[str] = None,
                tag: Optional[str] = None,
                last_n: Optional[int] = None) -> List[MemoryEntry]:
        out = self._entries
        if agent_name:
            out = [e for e in out if e.agent_name == agent_name]
        if tag:
            out = [e for e in out if tag in e.tags]
        if last_n:
            out = out[-last_n:]
        return out

    def as_text(self, last_n: int = 30) -> str:
        """Render recent memory as a string for LLM prompts."""
        lines: List[str] = []
        for e in self.entries(last_n=last_n):
            prefix = f"[{e.role}]"
            if e.agent_name:
                prefix = f"[{e.role}/{e.agent_name}]"
            lines.append(f"{prefix} {e.content}")
        return "\n".join(lines)

    # ── facts ───────────────────────────────────────────────────────

    def set_fact(self, key: str, value: Any) -> None:
        self._facts[key] = value

    def get_fact(self, key: str, default: Any = None) -> Any:
        return self._facts.get(key, default)

    @property
    def facts(self) -> Dict[str, Any]:
        return dict(self._facts)

    # ── schema cache ────────────────────────────────────────────────

    def cache_schema(self, table: str, columns: List[Dict[str, Any]]) -> None:
        self._schema_cache[table] = columns

    def get_schema(self, table: Optional[str] = None) -> Any:
        if table:
            return self._schema_cache.get(table)
        return dict(self._schema_cache)

    # ── reset ───────────────────────────────────────────────────────

    def clear(self) -> None:
        self._entries.clear()
        self._facts.clear()
        self._schema_cache.clear()

    def __len__(self) -> int:
        return len(self._entries)
