"""
AURA Shared Services Package
==============================
Cross-cutting infrastructure: config, logging, exceptions, middleware, service factory.
"""

from shared.config import settings
from shared.exceptions import (
    AuraError,
    AuthenticationError,
    DatabaseError,
    ForbiddenError,
    LLMError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from shared.logging_config import get_logger, setup_logging
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
