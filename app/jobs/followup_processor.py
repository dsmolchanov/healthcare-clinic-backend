# clinics/backend/app/jobs/followup_processor.py

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any
import asyncio

logger = logging.getLogger(__name__)

class FollowupProcessor:
    """Processes scheduled follow-ups that are due"""

    def __init__(self):
        from app.memory.conversation_memory import get_memory_manager
        self.manager = get_memory_manager()

    async def get_due_followups(self) -> List[Dict[str, Any]]:
        """Get all conversations with scheduled follow-ups that are now due"""

        now = datetime.now(timezone.utc)

        result = self.manager.supabase.table('conversation_sessions').select('*').lte(
            'scheduled_followup_at', now.isoformat()
        ).eq(
            'turn_status', 'agent_action_pending'
        ).is_(
            'ended_at', 'null'
        ).execute()

        logger.info(f"Found {len(result.data)} due follow-ups")
        return result.data

    async def send_followup_message(self, session: Dict[str, Any]):
        """Send follow-up message for a session"""

        session_id = session['id']
        user_identifier = session['user_identifier']
        followup_context = session.get('followup_context', {})
        last_agent_action = session.get('last_agent_action', '')

        logger.info(f"Processing follow-up for session {session_id}")

        # Construct follow-up message with context
        followup_message = {
            'type': 'system_initiated_followup',
            'session_id': session_id,
            'context': followup_context.get('context_summary', ''),
            'original_action': last_agent_action,
            'trigger': 'scheduled_followup'
        }

        # TODO: Send to agent orchestrator to generate actual response
        # For now, just log and update status

        try:
            # Update session to mark follow-up as processed
            self.manager.supabase.table('conversation_sessions').update({
                'scheduled_followup_at': None,  # Clear scheduled time
                'turn_status': 'agent_turn',  # Agent should respond now
                'updated_at': datetime.utcnow().isoformat(),
                'metadata': {
                    **session.get('metadata', {}),
                    'last_followup_processed': datetime.utcnow().isoformat()
                }
            }).eq('id', session_id).execute()

            logger.info(f"✅ Follow-up processed for {session_id}")

            # TODO: Actually trigger agent response via Evolution API or WhatsApp

        except Exception as e:
            logger.error(f"Failed to process follow-up for {session_id}: {e}")

    async def run(self):
        """Main job loop - call this from cron/scheduler"""

        logger.info("🔄 Running follow-up processor...")

        try:
            due_followups = await self.get_due_followups()

            if not due_followups:
                logger.info("No due follow-ups")
                return

            # Process each follow-up
            for session in due_followups:
                await self.send_followup_message(session)
                await asyncio.sleep(1)  # Rate limiting

            logger.info(f"✅ Processed {len(due_followups)} follow-ups")

        except Exception as e:
            logger.error(f"Follow-up processor failed: {e}", exc_info=True)

# CLI entry point
async def main():
    # Set up logging
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logger.info("Starting followup processor...")
    processor = FollowupProcessor()
    await processor.run()
    logger.info("Followup processor completed")

if __name__ == '__main__':
    asyncio.run(main())
