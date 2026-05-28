from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Optional

logger = logging.getLogger("aura.shared.secret_resolver")

if TYPE_CHECKING:  # pragma: no cover - typing helpers
    from azure.identity import DefaultAzureCredential as DefaultAzureCredentialType  # type: ignore
else:  # pragma: no cover - runtime fallbacks
    DefaultAzureCredentialType = Any

try:  # pragma: no cover - optional dependency
    from azure.identity import DefaultAzureCredential  # type: ignore
    from azure.keyvault.secrets import SecretClient  # type: ignore
except ImportError:  # pragma: no cover - azure packages optional in dev
    DefaultAzureCredential = None  # type: ignore
    SecretClient = None  # type: ignore


class SecretResolver:
    """Resolves secrets using Azure Key Vault when configured, or environment vars."""

    def __init__(self) -> None:
        self._vault_uri = os.getenv("KEY_VAULT_URI")
        self._client: Any = self._build_client()
        # Per-instance cache. @lru_cache on a method holds a strong
        # reference to self for every cached call, preventing GC of
        # the resolver instance (Ruff B019). A dict on the instance
        # ties the cache to the resolver's lifetime instead.
        self._cache: Dict[str, Optional[str]] = {}

    def _build_client(self) -> Any:
        if not self._vault_uri or not (DefaultAzureCredential and SecretClient):
            return None
        try:
            credential: DefaultAzureCredentialType = DefaultAzureCredential(  # type: ignore[call-arg]
                exclude_shared_token_cache_credential=True
            )
            return SecretClient(vault_url=self._vault_uri, credential=credential)  # type: ignore[return-value]
        except Exception as exc:
            logger.warning("Failed to build Azure Key Vault client: %s", exc)
            return None

    def get_secret(self, name: str) -> Optional[str]:
        if name in self._cache:
            return self._cache[name]
        value: Optional[str] = None
        if self._client:
            try:
                value = self._client.get_secret(name).value
            except Exception as exc:
                # Vault lookup failed — fall back to env. Log so chronic
                # vault outages are discoverable; previously this was
                # a silent ``pass`` that hid network errors and 403s.
                logger.warning(
                    "Key Vault lookup for %r failed (%s); falling back to env",
                    name, exc,
                )
        if value is None:
            value = os.getenv(name)
        self._cache[name] = value
        return value


secret_resolver = SecretResolver()
