"""Search previous conversation summaries."""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from app.config import get_supabase_client

logger = logging.getLogger(__name__)


class SummarySearchService:
    """Searches previous session summaries for user queries."""

    def __init__(self):
        self.supabase = get_supabase_client()

    async def search_summaries(
        self,
        phone_number: str,
        clinic_id: str,
        query: Optional[str] = None,
        days_back: int = 90,
        limit: int = 10
    ) -> List[Dict]:
        """
        Search previous session summaries.

        Args:
            phone_number: User's phone number
            clinic_id: Clinic context
            query: Optional search keywords
            days_back: How far back to search (default 90 days)
            limit: Max results

        Returns:
            List of session summaries with metadata
        """

        clean_phone = phone_number.replace("@s.whatsapp.net", "")
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)

        try:
            # Build query with correct Supabase API syntax
            query_builder = self.supabase.table('conversation_sessions').select(
                'id, session_summary, started_at, ended_at, metadata'
            ).eq(
                'user_identifier', clean_phone
            ).filter(
                'metadata->>clinic_id', 'eq', clinic_id
            ).eq(
                'status', 'closed'  # Only archived sessions
            ).not_.is_(
                'session_summary', 'null'
            ).gte(
                'started_at', cutoff_date.isoformat()
            ).order(
                'started_at', desc=True  # Most recent first
            )

            # Add text search if query provided (multilingual config)
            if query:
                # Use 'simple' config for multilingual support
                query_builder = query_builder.text_search(
                    'session_summary', query, config='simple', type='websearch'
                )

            result = query_builder.limit(limit).execute()

            if not result.data:
                logger.info(f"No summaries found for {clean_phone[:3]}*** (query: {query})")
                return []

            # Format results
            summaries = []
            for session in result.data:
                metadata = session.get('metadata', {}) or {}
                summaries.append({
                    'session_id': session['id'],
                    'summary': session['session_summary'],
                    'date': session['started_at'],
                    'ended': session.get('ended_at'),
                    'patient_name': metadata.get('patient_name', 'Unknown')
                })

            logger.info(f"Found {len(summaries)} summaries for {clean_phone[:3]}***")

            return summaries

        except Exception as e:
            logger.error(f"Error searching summaries: {e}", exc_info=True)
            return []
