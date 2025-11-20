"""Deep search of full conversation history."""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from app.config import get_supabase_client

logger = logging.getLogger(__name__)


class FullHistorySearchService:
    """Searches full message history (deeper than summaries)."""

    def __init__(self):
        self.supabase = get_supabase_client()

    async def search_full_history(
        self,
        phone_number: str,
        clinic_id: str,
        query: str,
        days_back: int = 90,
        limit: int = 20,
        offset: int = 0
    ) -> Dict:
        """
        Deep search of conversation messages.

        Args:
            phone_number: User's phone number
            clinic_id: Clinic context
            query: Search keywords (required)
            days_back: How far back to search
            limit: Results per page
            offset: Pagination offset

        Returns:
            Dict with messages and pagination info
        """

        clean_phone = phone_number.replace("@s.whatsapp.net", "")
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)

        try:
            # Get all user's session IDs
            sessions_result = self.supabase.table('conversation_sessions').select('id').eq(
                'user_identifier', clean_phone
            ).filter(
                'metadata->>clinic_id', 'eq', clinic_id
            ).gte(
                'started_at', cutoff_date.isoformat()
            ).execute()

            if not sessions_result.data:
                return {
                    'found': False,
                    'messages': [],
                    'total': 0,
                    'has_more': False
                }

            session_ids = [s['id'] for s in sessions_result.data]

            # Search message content
            # Build query with pagination
            query_builder = self.supabase.table('conversation_messages').select(
                'id, role, message_content, created_at, session_id',
                count='exact'  # Get total count for pagination
            ).in_(
                'session_id', session_ids
            )

            # Add text search (multilingual support)
            if query:
                query_builder = query_builder.text_search(
                    'message_content', query, config='simple', type='websearch'
                )

            # Apply pagination and ordering
            messages_result = query_builder.order(
                'created_at', desc=True  # Most recent first
            ).range(
                offset, offset + limit - 1
            ).execute()

            messages = messages_result.data if messages_result.data else []
            total_count = messages_result.count if hasattr(messages_result, 'count') else len(messages)
            has_more = (offset + limit) < total_count

            logger.info(f"Full history search: {len(messages)} results, total={total_count}, has_more={has_more}")

            return {
                'found': len(messages) > 0,
                'messages': messages,
                'total': total_count,
                'has_more': has_more,
                'offset': offset,
                'limit': limit
            }

        except Exception as e:
            logger.error(f"Error searching full history: {e}", exc_info=True)
            return {
                'found': False,
                'messages': [],
                'total': 0,
                'has_more': False,
                'error': str(e)
            }
