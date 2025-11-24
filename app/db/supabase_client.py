"""
DEPRECATED: HTTP optimization is now built into app.database.

For new code, use:
    from app.database import get_healthcare_client, get_main_client
"""

import warnings
from supabase import Client


def get_supabase_client(schema: str = 'healthcare') -> Client:
    """
    DEPRECATED: Use app.database.create_supabase_client() instead.

    HTTP/1.1 optimization is now built into the main database module.
    """
    warnings.warn(
        "app.db.supabase_client.get_supabase_client() is deprecated. "
        "Use app.database.get_healthcare_client() or create_supabase_client(schema) instead.",
        DeprecationWarning,
        stacklevel=2
    )
    from app.database import create_supabase_client
    return create_supabase_client(schema)