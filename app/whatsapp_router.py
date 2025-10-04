"""
WhatsApp Router for dual-provider support (Twilio and Evolution API)
Routes messages between providers based on tenant configuration
"""

import os
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from .whatsapp import send_whatsapp_message as send_twilio_message
from .evolution_api import EvolutionAPIClient
from .database import get_database_connection

logger = logging.getLogger(__name__)


class WhatsAppRouter:
    """Routes messages between Twilio and Evolution API based on tenant configuration"""

    def __init__(self):
        self.evolution_client = None
        self._evolution_initialized = False

    async def _init_evolution(self):
        """Initialize Evolution API client if not already done"""
        if not self._evolution_initialized:
            self.evolution_client = EvolutionAPIClient()
            await self.evolution_client.initialize()
            self._evolution_initialized = True

    async def get_tenant_config(self, tenant_id: str) -> Dict[str, Any]:
        """Get tenant WhatsApp configuration from database"""
        async with get_database_connection() as conn:
            # Check if tenant has Evolution instance
            evolution_result = await conn.fetchrow("""
                SELECT
                    ei.*,
                    ti.whatsapp_provider,
                    ti.evolution_config
                FROM evolution_instances ei
                JOIN tenant_integrations ti ON ti.tenant_id = ei.tenant_id
                WHERE ei.tenant_id = $1
                AND ei.status = 'connected'
                ORDER BY ei.last_connected_at DESC
                LIMIT 1
            """, tenant_id)

            if evolution_result:
                return {
                    'provider': 'evolution',
                    'evolution_instance_name': evolution_result['instance_name'],
                    'evolution_instance_id': evolution_result['id'],
                    'phone_number': evolution_result['phone_number'],
                    'config': evolution_result['evolution_config'] or {}
                }

            # Fall back to Twilio configuration
            twilio_result = await conn.fetchrow("""
                SELECT
                    ti.*,
                    wi.twilio_account_sid,
                    wi.whatsapp_number
                FROM tenant_integrations ti
                LEFT JOIN whatsapp_integrations wi ON wi.clinic_id = ti.tenant_id
                WHERE ti.tenant_id = $1
                AND ti.whatsapp_provider = 'twilio'
                LIMIT 1
            """, tenant_id)

            if twilio_result:
                return {
                    'provider': 'twilio',
                    'twilio_account_sid': twilio_result['twilio_account_sid'],
                    'whatsapp_number': twilio_result['whatsapp_number'],
                    'config': twilio_result['config'] or {}
                }

            # No configuration found
            return {
                'provider': None,
                'error': 'No WhatsApp configuration found for tenant'
            }

    async def route_message(self, tenant_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Route messages based on tenant configuration

        Args:
            tenant_id: Tenant identifier
            message: Message data with 'to', 'text', and optional 'media_url'

        Returns:
            Result dictionary with success status
        """
        try:
            config = await self.get_tenant_config(tenant_id)

            if not config.get('provider'):
                logger.error(f"No WhatsApp provider configured for tenant {tenant_id}")
                return {
                    'success': False,
                    'error': config.get('error', 'No provider configured')
                }

            if config['provider'] == 'evolution':
                await self._init_evolution()

                # Send through Evolution API
                instance_name = config['evolution_instance_name']

                if message.get('media_url'):
                    # Send media message
                    result = await self.evolution_client.send_media_message(
                        instance_name,
                        message['to'],
                        message['media_url'],
                        caption=message.get('text', ''),
                        media_type=message.get('media_type', 'image')
                    )
                else:
                    # Send text message
                    result = await self.evolution_client.send_text_message(
                        instance_name,
                        message['to'],
                        message['text']
                    )

                # Store message in database
                await self._store_evolution_message(
                    config['evolution_instance_id'],
                    message,
                    result,
                    'outbound'
                )

                return result

            else:  # Twilio
                # Send through Twilio
                result = await send_twilio_message(
                    message['to'],
                    message['text']
                )

                # Store message in database (existing Twilio tables)
                await self._store_twilio_message(tenant_id, message, result)

                return result

        except Exception as e:
            logger.error(f"Error routing message: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def handle_incoming(self, provider: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle incoming messages from either provider

        Args:
            provider: 'evolution' or 'twilio'
            data: Webhook data from provider

        Returns:
            Processing result
        """
        try:
            if provider == 'evolution':
                return await self._process_evolution_message(data)
            else:
                return await self._process_twilio_message(data)

        except Exception as e:
            logger.error(f"Error handling incoming message: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def _process_evolution_message(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming Evolution API message"""
        # Extract instance name from webhook path or data
        instance_name = data.get('instance', {}).get('instanceName')

        if not instance_name:
            return {
                'success': False,
                'error': 'Instance name not found in webhook data'
            }

        # Get tenant from instance
        async with get_database_connection() as conn:
            instance = await conn.fetchrow("""
                SELECT * FROM evolution_instances
                WHERE instance_name = $1
            """, instance_name)

            if not instance:
                return {
                    'success': False,
                    'error': f'Instance {instance_name} not found'
                }

        # Process based on event type
        event_type = data.get('event')

        if event_type == 'messages.upsert':
            # New message received
            message_data = data.get('data', {})

            # Extract message details
            from_number = message_data.get('key', {}).get('remoteJid', '').replace('@s.whatsapp.net', '')
            message_content = message_data.get('message', {})

            # Determine message type and content
            if message_content.get('conversation'):
                text = message_content['conversation']
                message_type = 'text'
            elif message_content.get('extendedTextMessage'):
                text = message_content['extendedTextMessage'].get('text', '')
                message_type = 'text'
            elif message_content.get('audioMessage'):
                # Voice note
                return await self._process_voice_note(instance, message_data)
            else:
                text = ''
                message_type = 'unknown'

            # Store incoming message
            await self._store_evolution_message(
                instance['id'],
                {
                    'from': from_number,
                    'text': text,
                    'type': message_type
                },
                message_data,
                'inbound'
            )

            # Process the message through existing logic
            from .whatsapp import process_whatsapp_message

            result = await process_whatsapp_message(
                instance['tenant_id'],
                from_number,
                text
            )

            return {
                'success': True,
                'processed': True,
                **result
            }

        elif event_type == 'connection.update':
            # Connection status update
            await self._handle_connection_update(instance_name, data)
            return {'success': True, 'event': 'connection_update'}

        elif event_type == 'qrcode.updated':
            # QR code updated
            await self._handle_qr_code_update(instance_name, data)
            return {'success': True, 'event': 'qrcode_update'}

        return {
            'success': True,
            'event': event_type,
            'processed': False
        }

    async def _process_twilio_message(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming Twilio message"""
        from .whatsapp import handle_whatsapp_webhook

        # Get tenant ID from webhook path or configuration
        tenant_id = data.get('tenant_id') or data.get('organization_id')

        if not tenant_id:
            # Try to get from phone number mapping
            to_number = data.get('To', '').replace('whatsapp:', '')
            async with get_database_connection() as conn:
                result = await conn.fetchrow("""
                    SELECT tenant_id FROM whatsapp_integrations
                    WHERE whatsapp_number = $1
                """, to_number)

                if result:
                    tenant_id = result['tenant_id']

        if not tenant_id:
            return {
                'success': False,
                'error': 'Tenant ID not found'
            }

        # Use existing Twilio handler
        return await handle_whatsapp_webhook(tenant_id, data)

    async def _process_voice_note(self, instance: Dict, message_data: Dict) -> Dict[str, Any]:
        """Process voice note through LiveKit"""
        # This would integrate with LiveKit for voice processing
        # For now, return a placeholder response
        return {
            'success': True,
            'processed': True,
            'type': 'voice_note',
            'message': 'Voice note received and being processed'
        }

    async def _handle_connection_update(self, instance_name: str, data: Dict[str, Any]):
        """Handle Evolution API connection status update"""
        connection_state = data.get('data', {}).get('state', {})
        status = connection_state.get('connection', 'disconnected')

        # Map Evolution states to our status
        status_map = {
            'open': 'connected',
            'connecting': 'connecting',
            'close': 'disconnected'
        }

        db_status = status_map.get(status, 'disconnected')

        # Update instance status in database
        async with get_database_connection() as conn:
            await conn.execute("""
                UPDATE evolution_instances
                SET
                    status = $1,
                    last_connected_at = CASE WHEN $1 = 'connected' THEN NOW() ELSE last_connected_at END,
                    updated_at = NOW()
                WHERE instance_name = $2
            """, db_status, instance_name)

    async def _handle_qr_code_update(self, instance_name: str, data: Dict[str, Any]):
        """Handle QR code update from Evolution API"""
        qr_data = data.get('data', {})
        qr_code = qr_data.get('qr') or qr_data.get('base64')

        if qr_code:
            # Update QR code in database
            async with get_database_connection() as conn:
                await conn.execute("""
                    UPDATE evolution_instances
                    SET
                        qr_code = $1,
                        qr_code_generated_at = NOW(),
                        status = 'qr_pending',
                        updated_at = NOW()
                    WHERE instance_name = $2
                """, qr_code, instance_name)

    async def _store_evolution_message(
        self,
        instance_id: str,
        message: Dict[str, Any],
        result: Dict[str, Any],
        direction: str
    ):
        """Store Evolution API message in database"""
        async with get_database_connection() as conn:
            await conn.execute("""
                INSERT INTO evolution_messages (
                    instance_id,
                    message_id,
                    phone_number,
                    direction,
                    message_type,
                    content,
                    media_url,
                    status,
                    metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
                instance_id,
                result.get('message_id') or result.get('key', {}).get('id'),
                message.get('to') or message.get('from'),
                direction,
                message.get('type', 'text'),
                message.get('text', ''),
                message.get('media_url'),
                'sent' if result.get('success') else 'failed',
                result
            )

    async def _store_twilio_message(
        self,
        tenant_id: str,
        message: Dict[str, Any],
        result: Dict[str, Any]
    ):
        """Store Twilio message in existing database structure"""
        async with get_database_connection() as conn:
            # This would use existing Twilio message storage
            # Placeholder for now
            pass

    async def get_provider_for_tenant(self, tenant_id: str) -> str:
        """Get the WhatsApp provider for a tenant"""
        config = await self.get_tenant_config(tenant_id)
        return config.get('provider', 'none')

    async def switch_provider(self, tenant_id: str, new_provider: str) -> Dict[str, Any]:
        """Switch WhatsApp provider for a tenant"""
        if new_provider not in ['twilio', 'evolution']:
            return {
                'success': False,
                'error': 'Invalid provider. Must be "twilio" or "evolution"'
            }

        async with get_database_connection() as conn:
            await conn.execute("""
                UPDATE tenant_integrations
                SET
                    whatsapp_provider = $1,
                    updated_at = NOW()
                WHERE tenant_id = $2
            """, new_provider, tenant_id)

        return {
            'success': True,
            'provider': new_provider
        }

    async def test_provider(self, tenant_id: str) -> Dict[str, Any]:
        """Test WhatsApp provider configuration"""
        config = await self.get_tenant_config(tenant_id)

        if not config.get('provider'):
            return {
                'success': False,
                'error': 'No provider configured'
            }

        if config['provider'] == 'evolution':
            await self._init_evolution()

            # Test Evolution connection
            status = await self.evolution_client.get_connection_status(
                config['evolution_instance_name']
            )

            return {
                'success': status.get('state') == 'open',
                'provider': 'evolution',
                'status': status
            }
        else:
            # Test Twilio (simplified)
            return {
                'success': bool(os.environ.get('TWILIO_ACCOUNT_SID')),
                'provider': 'twilio',
                'configured': True
            }


# Singleton instance
_router = None

def get_whatsapp_router() -> WhatsAppRouter:
    """Get singleton WhatsApp router instance"""
    global _router
    if _router is None:
        _router = WhatsAppRouter()
    return _router
