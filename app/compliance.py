"""
Compliance management for data retention and audit
"""

from datetime import datetime, timedelta
from typing import Dict, Any


async def enforce_retention_policy(market: str, retention_years: int) -> Dict[str, Any]:
    """
    Enforce data retention policy

    Args:
        market: Market identifier
        retention_years: Years to retain data

    Returns:
        Enforcement statistics
    """
    from .database import db

    cutoff_date = datetime.now() - timedelta(days=365 * retention_years)

    # Get all records
    all_records = await db.table('healthcare.appointments')\
        .select('*')\
        .execute()

    deleted = 0
    retained = 0

    for record in all_records.data if all_records else []:
        created_at = datetime.fromisoformat(record['created_at'])
        if created_at < cutoff_date:
            # Delete old record
            await db.table('healthcare.appointments')\
                .delete()\
                .eq('id', record['id'])\
                .execute()
            deleted += 1
        else:
            retained += 1

    return {
        'deleted': deleted,
        'retained': retained
    }
