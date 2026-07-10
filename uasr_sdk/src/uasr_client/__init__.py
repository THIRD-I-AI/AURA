"""uasr-client: Python SDK + CLI for the AURA UASR self-healing service."""
from .client import (
    AsyncUASRClient,
    UASRAPIError,
    UASRClient,
    UASRConnectionError,
    UASRError,
)
from .models import (
    BaselineResult,
    DeploymentInfo,
    GateResult,
    IngestResult,
    MetricsSnapshot,
    SourceInfo,
)

__version__ = "0.1.0"

__all__ = [
    "UASRClient",
    "AsyncUASRClient",
    "UASRError",
    "UASRConnectionError",
    "UASRAPIError",
    "BaselineResult",
    "IngestResult",
    "GateResult",
    "DeploymentInfo",
    "MetricsSnapshot",
    "SourceInfo",
    "__version__",
]
