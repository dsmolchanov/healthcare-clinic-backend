"""Generate AI summaries of conversation sessions."""

import logging
from datetime import datetime
from typing import Dict, List, Optional
from app.database import get_healthcare_client

logger = logging.getLogger(__name__)


class SessionSummarizer:
    """Generates concise summaries of conversation sessions."""

    def __init__(self):
        self.supabase = get_healthcare_client()
        self._llm_factory = None  # Lazy-loaded

    async def _get_llm_factory(self):
        """Lazy-load LLM factory to avoid blocking initialization"""
        if self._llm_factory is None:
            from app.services.llm.llm_factory import get_llm_factory
            self._llm_factory = await get_llm_factory()
        return self._llm_factory

    async def generate_summary(
        self,
        messages: List[Dict[str, any]],
        session_metadata: Dict[str, any],
        clinic_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> str:
        """
        Generate session summary from conversation messages.

        Args:
            messages: List of messages with role and content
            session_metadata: Session context (clinic_id, patient_name, etc.)
            clinic_id: Optional clinic ID for tier-based model routing
            session_id: Optional session ID for A/B experiment assignment

        Returns:
            Concise summary string
        """
        from app.services.llm.tiers import ModelTier

        if not messages:
            return "Empty session - no messages exchanged"

        # Build summary prompt
        conversation_text = self._format_messages(messages)

        system_prompt = """You are a medical conversation analyst. Generate a concise summary of this patient-clinic conversation.

Include:
1. PRIMARY INTENT: What did the patient want? (1 sentence)
2. KEY INFORMATION: Important details collected (2-3 bullet points)
3. OUTCOME: What happened? (booked/cancelled/pending/incomplete)
4. UNRESOLVED: What wasn't addressed? (if any)

Format as markdown. Be concise - max 150 words total."""

        user_prompt = f"""Session Metadata:
- Patient: {session_metadata.get('patient_name', 'Unknown')}
- Clinic: {session_metadata.get('clinic_id', 'Unknown')}
- Duration: {session_metadata.get('duration_minutes', 0)} minutes
- Messages: {len(messages)}

Conversation:
{conversation_text}

Generate summary:"""

        # Call LLM using tier-based routing
        messages_array = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # Use clinic_id from metadata if not provided
        resolved_clinic_id = clinic_id or session_metadata.get('clinic_id')

        try:
            llm_factory = await self._get_llm_factory()
            response = await llm_factory.generate_for_tier(
                tier=ModelTier.SUMMARIZATION,
                messages=messages_array,
                max_tokens=300,
                temperature=0.3,
                clinic_id=resolved_clinic_id,
                session_id=session_id
            )

            summary = response.content.strip() if response.content else ''

            logger.info(
                f"Generated summary: {len(summary)} chars, {len(messages)} messages "
                f"(tier={response.tier}, source={response.tier_source})"
            )

            return summary

        except Exception as e:
            logger.error(f"Error generating session summary: {e}", exc_info=True)
            return f"Summary generation failed: {str(e)}"

    def _format_messages(self, messages: List[Dict]) -> str:
        """Format messages for LLM prompt."""
        formatted = []
        for msg in messages[-20:]:  # Last 20 messages only
            role = msg.get('role', 'user')
            content = msg.get('content', '') or msg.get('message_content', '')
            formatted.append(f"{role.upper()}: {content}")

        return "\n".join(formatted)

    async def generate_and_store_summary(
        self,
        session_id: str,
        current_time: datetime
    ):
        """
        Generate summary and store in database (background task).

        This runs asynchronously and should not block request handling.
        """
        try:
            # Fetch session messages
            messages = await self._get_session_messages(session_id)

            if not messages:
                logger.warning(f"No messages found for session {session_id}, skipping summary")
                return

            # Fetch session metadata
            session_metadata = await self._get_session_metadata(session_id)

            # Generate summary using tier-based routing
            summary = await self.generate_summary(
                messages,
                session_metadata,
                clinic_id=session_metadata.get('clinic_id'),
                session_id=session_id
            )

            # Store summary with status (healthcare schema)
            self.supabase.table('conversation_sessions').update({
                'session_summary': summary,
                'summary_generated_at': current_time.isoformat(),
                'summary_status': 'ready'
            }).eq('id', session_id).execute()

            logger.info(f"✅ Stored summary for session {session_id[:8]}: {summary[:100]}...")

        except Exception as e:
            logger.error(f"Failed to generate/store summary for {session_id}: {e}", exc_info=True)

            # Mark as failed
            try:
                self.supabase.table('conversation_sessions').update({
                    'summary_status': 'failed'
                }).eq('id', session_id).execute()
            except Exception as update_error:
                logger.error(f"Failed to mark summary as failed: {update_error}")

    async def _get_session_messages(self, session_id: str) -> List[Dict]:
        """Fetch all messages for a session."""
        try:
            result = self.supabase.schema('healthcare').table('conversation_logs').select(
                'role, message_content, created_at'
            ).eq('session_id', session_id).order('created_at', desc=False).execute()

            return result.data if result.data else []
        except Exception as e:
            logger.error(f"Error fetching messages for session {session_id}: {e}")
            return []

    async def _get_session_metadata(self, session_id: str) -> Dict:
        """Fetch session metadata from healthcare schema."""
        try:
            result = self.supabase.table('conversation_sessions').select(
                'metadata, user_identifier, started_at, ended_at'
            ).eq('id', session_id).maybe_single().execute()

            if result.data:
                session = result.data
                started = datetime.fromisoformat(session['started_at'].replace('Z', '+00:00'))
                ended_str = session.get('ended_at')
                ended = datetime.fromisoformat(ended_str.replace('Z', '+00:00')) if ended_str else datetime.utcnow()
                duration_minutes = (ended - started).total_seconds() / 60

                metadata = session.get('metadata', {}) or {}
                return {
                    'patient_name': metadata.get('patient_name', 'Unknown'),
                    'clinic_id': metadata.get('clinic_id', 'Unknown'),
                    'duration_minutes': int(duration_minutes)
                }

            return {}
        except Exception as e:
            logger.error(f"Error fetching metadata for session {session_id}: {e}")
            return {}
