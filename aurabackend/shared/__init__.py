"""
AURA Shared Services Package
==============================
Cross-cutting infrastructure: config, logging, exceptions, middleware, service factory.
"""

from shared.config import settings
from shared.logging_config import get_logger, setup_logging
from shared.exceptions import (
    AuraError,
    ValidationError,
    NotFoundError,
    AuthenticationError,
    ForbiddenError,
    DatabaseError,
    LLMError,
    ServiceUnavailableError,
)
from shared.service_factory import create_service

__all__ = [
    "settings",
    "get_logger",
    "setup_logging",
    "create_service",
    "AuraError",
    "ValidationError",
    "NotFoundError",
    "AuthenticationError",
    "ForbiddenError",
    "DatabaseError",
    "LLMError",
    "ServiceUnavailableError",
]
