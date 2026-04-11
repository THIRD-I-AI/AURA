from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Optional

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

    def _build_client(self) -> Any:
        if not self._vault_uri or not (DefaultAzureCredential and SecretClient):
            return None
        try:
            credential: DefaultAzureCredentialType = DefaultAzureCredential(  # type: ignore[call-arg]
                exclude_shared_token_cache_credential=True
            )
            return SecretClient(vault_url=self._vault_uri, credential=credential)  # type: ignore[return-value]
        except Exception:
            return None

    @lru_cache(maxsize=128)
    def get_secret(self, name: str) -> Optional[str]:
        if self._client:
            try:
                return self._client.get_secret(name).value
            except Exception:
                pass
        return os.getenv(name)


secret_resolver = SecretResolver()
