"""BUG-431: the gateway reads the upstream addresses under the names deployments set."""
from __future__ import annotations

import inspect

from api_gateway.routers import connections, queries


def test_the_sandbox_and_connectors_addresses_use_the_configured_env_names():
    assert 'os.getenv("AURA_SANDBOX_URL"' in inspect.getsource(queries)
    assert "EXECUTION_SANDBOX_URL\"" not in inspect.getsource(queries).replace("read EXECUTION_SANDBOX_URL", "")
    assert 'os.getenv("AURA_CONNECTORS_URL"' in inspect.getsource(connections)
    assert 'os.getenv("DATABASE_SERVICE_URL"' not in inspect.getsource(connections)
