"""
Unified session management - canonical interface.

This module provides a single entry point for all session-related functionality.
Uses Redis for session state storage with lifecycle management.

Supports FastAPI dependency injection for testability.

Usage:
    from app.services.session import get_session_manager, get_session_storage
    from app.services.session import SessionManager, ResetType

For FastAPI:
    from app.services.session import session_manager_dependency

    @app.post("/api/endpoint")
    async def endpoint(session_mgr = Depends(session_manager_dependency)):
        ...
"""
from typing import Optional

from fastapi import Depends

# Re-export from lifecycle manager (temporal segmentation, reset logic)
from app.services.session_manager import (
    SessionManager,
    SessionState,
    ResetType,
    SessionSplitSignal,
)

# Re-export from storage manager (Redis persistence)
from app.services.redis_session_manager import RedisSessionManager

# Type aliases for clarity
SessionStorage = RedisSessionManager

# Singleton instances (lazy initialization)
_session_manager: Optional[SessionManager] = None
_session_storage: Optional[SessionStorage] = None


def get_session_manager() -> SessionManager:
    """
    Get singleton session lifecycle manager.

    Uses lazy initialization to avoid import-time connections.
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


def get_session_storage() -> SessionStorage:
    """
    Get singleton session storage (Redis-backed).

    Uses lazy initialization to avoid import-time Redis connections.
    """
    global _session_storage
    if _session_storage is None:
        _session_storage = RedisSessionManager()
    return _session_storage


# FastAPI dependencies for injection
async def session_manager_dependency() -> SessionManager:
    """FastAPI dependency for session lifecycle manager injection."""
    return get_session_manager()


async def session_storage_dependency() -> SessionStorage:
    """FastAPI dependency for session storage injection."""
    return get_session_storage()


def reset_for_tests() -> None:
    """Reset singletons for test isolation."""
    global _session_manager, _session_storage
    _session_manager = None
    _session_storage = None


# Backwards compatibility aliases
# TODO: Remove after migrating all callers
def get_redis_session_manager() -> RedisSessionManager:
    """Deprecated: Use get_session_storage() instead."""
    return get_session_storage()
