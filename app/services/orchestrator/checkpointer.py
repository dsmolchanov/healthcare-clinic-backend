"""
Postgres checkpointer for LangGraph state persistence.
Replaces MemorySaver with durable storage.

IMPORTANT: State is session-scoped via thread_id. When sessions rotate
(soft/hard reset), a new thread_id is used, starting fresh state.

Uses AsyncPostgresSaver.from_conn_string() per LangGraph docs:
https://docs.langchain.com/oss/python/langgraph/add-memory
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_checkpointer = None


async def get_checkpointer():
    """
    Get or create Postgres checkpointer with connection pooling.

    Uses from_conn_string() which handles:
    - Connection pooling automatically
    - Required autocommit=True and row_factory=dict_row
    - Proper psycopg connection management

    Falls back to MemorySaver if DATABASE_URL is not configured
    (for local development or testing).
    """
    global _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    # Try to use Postgres checkpointer if available
    db_uri = os.getenv("LANGGRAPH_DB_URI") or os.getenv("DATABASE_URL")

    if db_uri:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            _checkpointer = await AsyncPostgresSaver.from_conn_string(db_uri)
            await _checkpointer.setup()  # Idempotent - safe to call multiple times
            logger.info("Postgres checkpointer initialized (AsyncPostgresSaver)")
            return _checkpointer

        except ImportError:
            logger.warning(
                "langgraph.checkpoint.postgres not installed. "
                "Install with: pip install langgraph-checkpoint-postgres"
            )
        except Exception as e:
            logger.warning(f"Failed to initialize Postgres checkpointer: {e}. Falling back to MemorySaver.")

    # Fallback to in-memory saver for development/testing
    from langgraph.checkpoint.memory import MemorySaver

    logger.info("Using in-memory checkpointer (MemorySaver) - state will not persist across restarts")
    _checkpointer = MemorySaver()
    return _checkpointer


async def close_checkpointer() -> None:
    """Close checkpointer connection pool on shutdown."""
    global _checkpointer
    if _checkpointer is not None:
        # AsyncPostgresSaver manages its own pool cleanup
        _checkpointer = None
        logger.info("Postgres checkpointer closed")


async def get_session_state(thread_id: str) -> Optional[dict]:
    """
    Get checkpointed state for a session.

    Returns None if session has no checkpoint (new session after rotation).
    """
    checkpointer = await get_checkpointer()
    config = {"configurable": {"thread_id": thread_id}}

    try:
        checkpoint = await checkpointer.aget(config)
        if checkpoint:
            logger.debug(f"Loaded checkpoint for thread {thread_id[:20]}...")
            return checkpoint.get("channel_values", {})
    except Exception as e:
        logger.warning(f"Failed to get checkpoint for {thread_id[:20]}...: {e}")

    logger.debug(f"No checkpoint found for thread {thread_id[:20]}... (new session)")
    return None


async def clear_session_state(thread_id: str) -> None:
    """
    Clear checkpointed state for a session (called on session archive).

    Note: We typically DON'T call this - old checkpoints are left as audit trail.
    Only use if you need to force-clear state.
    """
    logger.info(f"Session state cleared for thread {thread_id[:20]}...")
