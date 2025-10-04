"""
WhatsApp Message Processor Service
Handles async processing of WhatsApp messages with AI/RAG
"""

import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime
import asyncio
import httpx

from app.database import get_supabase_client
from app.memory.conversation_memory import ConversationMemory
from app.services.multilingual_message_processor import MultilingualMessageProcessor

logger = logging.getLogger(__name__)


class WhatsAppMessageProcessor:
    """Processes WhatsApp messages asynchronously"""

    def __init__(self):
        self.supabase = get_supabase_client()
        self.memory = ConversationMemory()
        self.message_processor = MultilingualMessageProcessor()
        self.http_client = httpx.AsyncClient(timeout=60.0)

    async def process_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a WhatsApp message with AI/RAG
        """
        try:
            # Extract message details
            data = message_data.get('data', {})
            message_info = data.get('message', {})
            from_number = message_info.get('from', '').replace('@s.whatsapp.net', '')
            text = message_info.get('text', '')
            instance_name = data.get('instanceName', 'unknown')

            if not from_number or not text:
                logger.warning(f"Invalid message data: from={from_number}, text_len={len(text)}")
                return {"status": "skipped", "reason": "invalid_data"}

            logger.info(f"Processing message from {from_number}: {text[:50]}...")

            # Get or create conversation session
            session_id = await self._get_or_create_session(from_number, instance_name)

            # Process with AI (language detection, intent, response generation)
            try:
                response = await self._process_with_ai(
                    session_id=session_id,
                    from_number=from_number,
                    message_text=text
                )

                # Send response back via Evolution API
                if response and response.get('text'):
                    await self._send_whatsapp_response(
                        instance_name=instance_name,
                        to_number=from_number,
                        response_text=response['text']
                    )

                    # Log successful processing
                    await self._log_message(
                        session_id=session_id,
                        from_number=from_number,
                        message_text=text,
                        response_text=response['text'],
                        status='success'
                    )

                    return {
                        "status": "success",
                        "session_id": session_id,
                        "response_sent": True
                    }

            except Exception as e:
                logger.error(f"AI processing error: {str(e)}")
                # Send error message to user
                await self._send_whatsapp_response(
                    instance_name=instance_name,
                    to_number=from_number,
                    response_text="Lo siento, hubo un error procesando tu mensaje. Por favor intenta de nuevo."
                )
                raise

        except Exception as e:
            logger.error(f"Message processing error: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            }

    async def _get_or_create_session(self, phone_number: str, instance_name: str) -> str:
        """Get or create a conversation session"""
        try:
            # Check for existing session
            result = self.supabase.table('conversation_sessions').select('*').eq(
                'phone_number', phone_number
            ).eq('channel', 'whatsapp').execute()

            if result.data and len(result.data) > 0:
                session = result.data[0]
                session_id = session['id']
                logger.info(f"Found existing session: {session_id}")
            else:
                # Create new session
                new_session = {
                    'phone_number': phone_number,
                    'channel': 'whatsapp',
                    'instance_name': instance_name,
                    'status': 'active',
                    'metadata': {
                        'source': 'evolution_api',
                        'created_via': 'webhook'
                    }
                }
                result = self.supabase.table('conversation_sessions').insert(new_session).execute()
                session_id = result.data[0]['id']
                logger.info(f"Created new session: {session_id}")

            return session_id

        except Exception as e:
            logger.error(f"Session management error: {str(e)}")
            # Generate fallback session ID
            return f"temp_{phone_number}_{datetime.utcnow().timestamp()}"

    async def _process_with_ai(
        self,
        session_id: str,
        from_number: str,
        message_text: str
    ) -> Dict[str, Any]:
        """Process message with AI/RAG system"""
        try:
            # Get conversation history
            history = await self.memory.get_conversation_history(session_id, limit=10)

            # Process with multilingual AI
            response = await self.message_processor.process_message(
                message=message_text,
                session_id=session_id,
                user_id=from_number,
                conversation_history=history
            )

            # Store in memory
            await self.memory.add_message(
                session_id=session_id,
                role='user',
                content=message_text
            )
            await self.memory.add_message(
                session_id=session_id,
                role='assistant',
                content=response.get('text', '')
            )

            return response

        except Exception as e:
            logger.error(f"AI processing error: {str(e)}")
            # Return a fallback response
            return {
                "text": "Disculpa, estoy teniendo problemas técnicos. Por favor intenta más tarde o llama directamente a la clínica.",
                "error": str(e)
            }

    async def _send_whatsapp_response(
        self,
        instance_name: str,
        to_number: str,
        response_text: str
    ) -> bool:
        """Send response back via Evolution API"""
        try:
            # Format phone number
            if not to_number.endswith('@s.whatsapp.net'):
                to_number = f"{to_number}@s.whatsapp.net"

            # Send via Evolution API
            evolution_url = "https://evolution-api-prod.fly.dev"
            endpoint = f"{evolution_url}/message/sendText/{instance_name}"

            payload = {
                "number": to_number,
                "text": response_text,
                "delay": 1000  # 1 second delay for natural feel
            }

            response = await self.http_client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                logger.info(f"Response sent successfully to {to_number}")
                return True
            else:
                logger.error(f"Failed to send response: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Error sending WhatsApp response: {str(e)}")
            return False

    async def _log_message(
        self,
        session_id: str,
        from_number: str,
        message_text: str,
        response_text: str,
        status: str
    ):
        """Log message to database"""
        try:
            log_entry = {
                'session_id': session_id,
                'phone_number': from_number,
                'user_message': message_text,
                'bot_response': response_text,
                'status': status,
                'channel': 'whatsapp',
                'created_at': datetime.utcnow().isoformat()
            }

            self.supabase.table('whatsapp_messages').insert(log_entry).execute()
            logger.info(f"Message logged for session {session_id}")

        except Exception as e:
            logger.error(f"Error logging message: {str(e)}")

    async def close(self):
        """Cleanup resources"""
        await self.http_client.aclose()