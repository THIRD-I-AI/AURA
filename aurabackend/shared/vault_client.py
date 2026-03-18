"""
AURA Vault Client
=================
High-level convenience wrapper around ``DatabaseAdapter`` that provides
domain-specific methods for AURA's three data planes:

  • **Regular**  — users, transactions, data-sources, saved queries
  • **Image/AI** — embedding storage & similarity search
  • **VR / 4D**  — spatial telemetry, environment objects

Usage
-----
    from shared.vault_client import vault

    await vault.connect()
    users = await vault.get_top_customers(limit=5)
    similar = await vault.find_similar_images(embedding, limit=10)
    await vault.store_vr_frame(user_id, x=1.0, y=2.0, z=3.0, velocity=[0.1, 0.2, 0.3])
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

from shared.database_adapter import DatabaseAdapter, get_adapter


class AuraVault:
    """Domain-aware facade over the universal adapter."""

    def __init__(self, adapter: Optional[DatabaseAdapter] = None) -> None:
        self._db = adapter or get_adapter(cache_key="vault")

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                          #
    # ------------------------------------------------------------------ #

    async def connect(self) -> bool:
        return await self._db.connect()

    async def disconnect(self) -> bool:
        return await self._db.disconnect()

    async def health(self) -> Dict[str, Any]:
        return await self._db.health_check()

    async def capabilities(self) -> Dict[str, bool]:
        return await self._db.capabilities()

    # ================================================================== #
    #  REGULAR DATA                                                       #
    # ================================================================== #

    async def get_top_customers(self, limit: int = 10) -> List[Dict[str, Any]]:
        return await self._db.execute_query(
            "SELECT u.email, u.display_name, SUM(t.amount) AS total_spend "
            "FROM users u JOIN transactions t ON u.user_id = t.user_id "
            "GROUP BY u.email, u.display_name "
            "ORDER BY total_spend DESC "
            f"LIMIT {limit}"
        )

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        rows = await self._db.execute_query(
            "SELECT * FROM users WHERE user_id = $1", [user_id], limit=1,
        )
        return rows[0] if rows else None

    async def create_user(
        self, email: str, display_name: str = "", tier: str = "free",
    ) -> Optional[str]:
        rows = await self._db.execute_query(
            "INSERT INTO users (email, display_name, subscription_tier) "
            "VALUES ($1, $2, $3) RETURNING user_id",
            [email, display_name, tier],
        )
        return str(rows[0]["user_id"]) if rows else None

    async def register_data_source(
        self,
        user_id: str,
        source_type: str,
        display_name: str,
        config: Dict[str, Any],
    ) -> Optional[str]:
        import json as _json
        rows = await self._db.execute_query(
            "INSERT INTO data_sources (user_id, source_type, display_name, config) "
            "VALUES ($1, $2, $3, $4::jsonb) RETURNING source_id",
            [user_id, source_type, display_name, _json.dumps(config)],
        )
        return str(rows[0]["source_id"]) if rows else None

    async def log_audit(
        self, user_id: Optional[str], action: str, resource: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        import json as _json
        await self._db.execute_write(
            "INSERT INTO audit_log (user_id, action, resource, details) "
            "VALUES ($1, $2, $3, $4::jsonb)",
            [user_id, action, resource, _json.dumps(details or {})],
        )

    async def save_query(
        self,
        user_id: str,
        title: str,
        sql_text: str,
        description: str = "",
        chart_config: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        import json as _json
        rows = await self._db.execute_query(
            "INSERT INTO saved_queries (user_id, title, sql_text, description, chart_config) "
            "VALUES ($1, $2, $3, $4, $5::jsonb) RETURNING query_id",
            [user_id, title, sql_text, description,
             _json.dumps(chart_config or {})],
        )
        return str(rows[0]["query_id"]) if rows else None

    # ================================================================== #
    #  AGENT MEMORY                                                       #
    # ================================================================== #

    async def store_memory(
        self,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        embedding: Optional[List[float]] = None,
    ) -> Optional[str]:
        if embedding:
            return await self._db.store_vector(
                "agent_memory",
                {"session_id": session_id, "user_id": user_id,
                 "role": role, "content": content},
                embedding,
            )
        rows = await self._db.execute_query(
            "INSERT INTO agent_memory (session_id, user_id, role, content) "
            "VALUES ($1, $2, $3, $4) RETURNING memory_id",
            [session_id, user_id, role, content],
        )
        return str(rows[0]["memory_id"]) if rows else None

    async def recall_memory(
        self,
        embedding: List[float],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Semantic recall — find memories most similar to an embedding."""
        return await self._db.vector_search(
            "agent_memory", embedding, limit=limit,
        )

    # ================================================================== #
    #  IMAGE / AI EMBEDDINGS                                              #
    # ================================================================== #

    async def store_image(
        self,
        file_name: str,
        storage_url: str,
        embedding: List[float],
        *,
        user_id: Optional[str] = None,
        labels: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        import json as _json
        data: Dict[str, Any] = {
            "file_name": file_name,
            "storage_url": storage_url,
        }
        if user_id:
            data["user_id"] = user_id
        if labels:
            data["labels"] = labels
        if metadata:
            data["metadata"] = _json.dumps(metadata)
        return await self._db.store_vector("image_assets", data, embedding)

    async def find_similar_images(
        self,
        embedding: List[float],
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        return await self._db.vector_search(
            "image_assets", embedding, limit=limit, metric="cosine",
        )

    # ================================================================== #
    #  GENERIC VECTOR STORE                                               #
    # ================================================================== #

    async def store_embedding(
        self,
        collection: str,
        embedding: List[float],
        content: str = "",
        source_ref: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        import json as _json
        data: Dict[str, Any] = {
            "collection": collection,
            "content": content,
            "source_ref": source_ref,
        }
        if metadata:
            data["metadata"] = _json.dumps(metadata)
        return await self._db.store_vector("vector_store", data, embedding)

    async def search_embeddings(
        self,
        collection: str,
        embedding: List[float],
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        # Filter by collection after vector search
        results = await self._db.vector_search(
            "vector_store", embedding, limit=limit * 3,
        )
        return [r for r in results if r.get("collection") == collection][:limit]

    # ================================================================== #
    #  VR / 4D SPATIAL                                                    #
    # ================================================================== #

    async def store_vr_frame(
        self,
        user_id: str,
        x: float,
        y: float,
        z: float,
        *,
        velocity: Optional[List[float]] = None,
        orientation: Optional[List[float]] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        import json as _json
        data: Dict[str, Any] = {"user_id": user_id}
        if session_id:
            data["session_id"] = session_id
        if velocity:
            data["velocity"] = str(velocity)
        if orientation:
            data["orientation"] = str(orientation)
        if metadata:
            data["metadata"] = _json.dumps(metadata)
        return await self._db.store_point(
            "vr_telemetry", data, x, y, z,
        )

    async def get_user_vr_path(
        self,
        user_id: str,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        return await self._db.spatial_query(
            "SELECT telemetry_id, ST_AsText(location) AS position, "
            "velocity, orientation, captured_at "
            "FROM vr_telemetry WHERE user_id = $1 "
            "ORDER BY captured_at DESC "
            f"LIMIT {limit}",
            [user_id],
        )

    async def find_users_in_area(
        self,
        x: float, y: float, z: float,
        radius_meters: float = 100.0,
    ) -> List[Dict[str, Any]]:
        return await self._db.spatial_query(
            "SELECT DISTINCT user_id, ST_AsText(location) AS position, captured_at "
            "FROM vr_telemetry "
            "WHERE ST_DWithin(location::geography, "
            "ST_SetSRID(ST_MakePoint($1, $2, $3), 4326)::geography, $4) "
            "ORDER BY captured_at DESC",
            [x, y, z, radius_meters],
        )

    async def link_vr_to_purchase(
        self, user_id: str,
    ) -> List[Dict[str, Any]]:
        """Cross-domain: link VR movement to financial transactions."""
        return await self._db.execute_query(
            "SELECT ST_AsText(v.location) AS vr_position, "
            "v.captured_at AS vr_time, t.amount, t.currency, t.category "
            "FROM vr_telemetry v "
            "JOIN transactions t ON v.user_id = t.user_id "
            "WHERE v.user_id = $1 "
            "ORDER BY v.captured_at DESC",
            [user_id],
        )

    # ================================================================== #
    #  VR ENVIRONMENTS & OBJECTS                                          #
    # ================================================================== #

    async def create_vr_environment(
        self, name: str, description: str = "",
        config: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        import json as _json
        rows = await self._db.execute_query(
            "INSERT INTO vr_environments (name, description, config) "
            "VALUES ($1, $2, $3::jsonb) RETURNING env_id",
            [name, description, _json.dumps(config or {})],
        )
        return str(rows[0]["env_id"]) if rows else None

    async def store_vr_object(
        self,
        env_id: str,
        object_type: str,
        x: float, y: float, z: float,
        *,
        label: str = "",
        mesh_embedding: Optional[List[float]] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        import json as _json
        data: Dict[str, Any] = {
            "env_id": env_id,
            "object_type": object_type,
        }
        if label:
            data["label"] = label
        if properties:
            data["properties"] = _json.dumps(properties)
        # Use store_point for the position
        return await self._db.store_point(
            "vr_objects", data, x, y, z, geom_column="position",
        )


# ======================== Module-level singleton ========================

vault = AuraVault()
