"""
WhatsApp-to-Clinic Prewarm Cache Service

Maintains a Redis-backed cache that maps WhatsApp instance names to clinic info.
This eliminates the need for database queries on every incoming message.

Key benefits:
- Zero DB queries on hot path (messages/webhooks)
- Sub-millisecond lookup time
- Survives process restarts
- Works across multiple workers

Cache structure:
    Key: whatsapp:instance:{instance_name}
    Value: JSON with {clinic_id, organization_id, name, whatsapp_number}
    TTL: 1 hour (refreshed on warmup)
"""

import json
import logging
from typing import Dict, Any, Optional, List
from app.config import get_redis_client
from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


class WhatsAppClinicCache:
    """Manages WhatsApp instance → clinic mapping cache"""

    def __init__(self, redis_client=None, ttl: int = 3600):
        """
        Initialize cache manager

        Args:
            redis_client: Redis client (defaults to shared instance)
            ttl: Cache TTL in seconds (default 1h)
        """
        self.redis = redis_client or get_redis_client()
        self.ttl = ttl

    def _make_key(self, instance_name: str) -> str:
        """Generate cache key for instance"""
        return f"whatsapp:instance:{instance_name}"

    def _make_token_key(self, webhook_token: str) -> str:
        """Generate cache key for webhook token lookup"""
        return f"whatsapp:token:{webhook_token}"

    async def get_clinic_info(self, instance_name: str) -> Optional[Dict[str, Any]]:
        """
        Get clinic info for WhatsApp instance from cache

        Uses JSON by default, falls back to pickle on decode errors.

        Returns:
            Dict with clinic_id, organization_id, name, whatsapp_number
            None if not found in cache
        """
        cache_key = self._make_key(instance_name)

        try:
            cached_bytes = self.redis.get(cache_key)
            if not cached_bytes:
                logger.debug(f"❌ Cache MISS: instance {instance_name}")
                return None

            # Try JSON first (faster, human-readable)
            try:
                clinic_info = json.loads(cached_bytes)
                logger.debug(f"✅ Cache HIT: clinic info for instance {instance_name} (JSON)")
                return clinic_info
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Fallback to pickle for binary data
                import pickle
                clinic_info = pickle.loads(cached_bytes)
                logger.debug(f"✅ Cache HIT: clinic info for instance {instance_name} (pickle)")
                return clinic_info

        except Exception as e:
            logger.error(f"Redis read error for instance {instance_name}: {e}")
            return None

    async def set_clinic_info(
        self,
        instance_name: str,
        clinic_id: str,
        organization_id: str,
        name: str,
        whatsapp_number: Optional[str] = None
    ):
        """
        Cache clinic info for WhatsApp instance

        Tries JSON first, falls back to pickle if serialization fails.

        Args:
            instance_name: WhatsApp instance identifier
            clinic_id: Clinic UUID
            organization_id: Organization UUID
            name: Clinic name
            whatsapp_number: WhatsApp business number
        """
        cache_key = self._make_key(instance_name)

        clinic_info = {
            "clinic_id": clinic_id,
            "organization_id": organization_id,
            "name": name,
            "whatsapp_number": whatsapp_number,
            "instance_name": instance_name
        }

        try:
            # Try JSON first (preferred: human-readable, faster)
            try:
                serialized = json.dumps(clinic_info, ensure_ascii=False)
                self.redis.setex(cache_key, self.ttl, serialized)
                logger.debug(f"✅ Cached clinic info for instance {instance_name} (JSON)")
            except (TypeError, ValueError):
                # Fallback to pickle for complex objects
                import pickle
                serialized = pickle.dumps(clinic_info)
                self.redis.setex(cache_key, self.ttl, serialized)
                logger.debug(f"✅ Cached clinic info for instance {instance_name} (pickle fallback)")

        except Exception as e:
            logger.error(f"Redis write error for instance {instance_name}: {e}")

    async def get_clinic_info_by_token(self, webhook_token: str) -> Optional[Dict[str, Any]]:
        """
        Get clinic info for webhook token from cache

        This is the NEW primary lookup method for webhook routing.

        Returns:
            Dict with clinic_id, organization_id, name, instance_name, phone_number
            None if not found in cache
        """
        cache_key = self._make_token_key(webhook_token)

        try:
            cached_bytes = self.redis.get(cache_key)
            if not cached_bytes:
                logger.debug(f"❌ Token cache MISS: {webhook_token[:8]}...")
                return None

            # Try JSON first (faster)
            try:
                clinic_info = json.loads(cached_bytes)
                logger.debug(f"✅ Token cache HIT: {webhook_token[:8]}... → clinic {clinic_info.get('clinic_id', '')[:8]}...")
                return clinic_info
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Fallback to pickle
                import pickle
                clinic_info = pickle.loads(cached_bytes)
                logger.debug(f"✅ Token cache HIT (pickle): {webhook_token[:8]}...")
                return clinic_info

        except Exception as e:
            logger.error(f"Redis read error for token {webhook_token[:8]}...: {e}")
            return None

    async def set_clinic_info_by_token(
        self,
        webhook_token: str,
        clinic_id: str,
        organization_id: str,
        name: str,
        instance_name: Optional[str] = None,
        phone_number: Optional[str] = None
    ):
        """
        Cache clinic info by webhook token

        Args:
            webhook_token: Webhook routing token
            clinic_id: Clinic UUID
            organization_id: Organization UUID
            name: Clinic name
            instance_name: Evolution instance name (optional, for backwards compat)
            phone_number: WhatsApp business number
        """
        cache_key = self._make_token_key(webhook_token)

        clinic_info = {
            "clinic_id": clinic_id,
            "organization_id": organization_id,
            "name": name,
            "instance_name": instance_name,  # For backwards compat
            "phone_number": phone_number,
            "webhook_token": webhook_token
        }

        try:
            serialized = json.dumps(clinic_info, ensure_ascii=False)
            self.redis.setex(cache_key, self.ttl, serialized)
            logger.debug(f"✅ Cached clinic info for token {webhook_token[:8]}...")
        except Exception as e:
            logger.error(f"Redis write error for token {webhook_token[:8]}...: {e}")

    async def get_or_fetch_clinic_info_by_token(
        self,
        webhook_token: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get clinic info from cache, or fetch from DB if not cached

        This method fixes the N+1 query issue by using indexed lookup.

        Returns:
            Dict with clinic info or None
        """
        # Try cache first
        cached = await self.get_clinic_info_by_token(webhook_token)
        if cached:
            return cached

        # Cache miss - fetch from DB with INDEXED query (fixes N+1!)
        logger.info(f"Token cache miss for {webhook_token[:8]}..., fetching from DB...")

        try:
            supabase = get_supabase_client()

            # Single indexed query (NOT N+1!)
            result = supabase.schema('healthcare').table('integrations').select(
                'organization_id, clinic_id, config, phone_number, display_name'
            ).eq('type', 'whatsapp').eq('enabled', True).eq(
                'webhook_token', webhook_token  # Indexed lookup!
            ).limit(1).execute()

            if not result.data or len(result.data) == 0:
                logger.warning(f"No integration found for token {webhook_token[:8]}...")
                return None

            integration = result.data[0]
            config = integration.get('config', {})

            # Get clinic name (already have clinic_id from integration)
            clinic_id = integration['clinic_id']
            clinic_result = supabase.table('clinics').select(
                'name'
            ).eq('id', clinic_id).limit(1).execute()

            clinic_name = clinic_result.data[0]['name'] if clinic_result.data else integration.get('display_name')

            # Cache it for next time
            await self.set_clinic_info_by_token(
                webhook_token=webhook_token,
                clinic_id=clinic_id,
                organization_id=integration['organization_id'],
                name=clinic_name,
                instance_name=config.get('instance_name'),  # Use standard key
                phone_number=integration.get('phone_number')
            )

            return {
                "clinic_id": clinic_id,
                "organization_id": integration['organization_id'],
                "name": clinic_name,
                "instance_name": config.get('instance_name'),
                "phone_number": integration.get('phone_number'),
                "webhook_token": webhook_token
            }

        except Exception as e:
            logger.error(f"Failed to fetch clinic info for token {webhook_token[:8]}...: {e}")
            return None

    async def warmup_all_instances(self) -> Dict[str, Any]:
        """
        Preload all active WhatsApp instances into cache

        Now caches BOTH instance_name and webhook_token mappings.

        Returns:
            Dict with warmup statistics
        """
        supabase = get_supabase_client()
        stats = {
            "total": 0,
            "cached": 0,
            "tokens_cached": 0,  # NEW
            "errors": 0,
            "instances": []
        }

        try:
            # Query healthcare.integrations for WhatsApp configurations
            result = supabase.schema('healthcare').table('integrations').select(
                'id, organization_id, clinic_id, type, config, enabled, webhook_token, phone_number, display_name'
            ).eq('type', 'whatsapp').eq('enabled', True).execute()

            integrations = result.data if result.data else []
            stats["total"] = len(integrations)

            logger.info(f"🔥 Warming WhatsApp cache for {len(integrations)} instance(s)...")

            # Batch clinic lookup to avoid N+1
            clinic_ids = [i['clinic_id'] for i in integrations]
            clinics_result = supabase.table('clinics').select(
                'id, organization_id, name'
            ).in_('id', clinic_ids).execute()

            clinics_by_id = {c['id']: c for c in (clinics_result.data or [])}

            for integration in integrations:
                try:
                    clinic_id = integration.get('clinic_id')
                    org_id = integration.get('organization_id')
                    config = integration.get('config', {})
                    instance_name = config.get('instance_name')
                    webhook_token = integration.get('webhook_token')

                    if not webhook_token:
                        logger.warning(f"Skipping integration {integration.get('id')}: missing webhook_token")
                        stats["errors"] += 1
                        continue

                    clinic = clinics_by_id.get(clinic_id)
                    if not clinic:
                        logger.warning(f"No clinic found for clinic_id {clinic_id}")
                        stats["errors"] += 1
                        continue

                    clinic_name = clinic['name']
                    phone_number = integration.get('phone_number') or config.get('phone_number')

                    # Cache by webhook_token (PRIMARY)
                    await self.set_clinic_info_by_token(
                        webhook_token=webhook_token,
                        clinic_id=clinic_id,
                        organization_id=org_id,
                        name=clinic_name,
                        instance_name=instance_name,
                        phone_number=phone_number
                    )
                    stats["tokens_cached"] += 1

                    # ALSO cache by instance_name (for backwards compat during migration)
                    if instance_name:
                        await self.set_clinic_info(
                            instance_name=instance_name,
                            clinic_id=clinic_id,
                            organization_id=org_id,
                            name=clinic_name,
                            whatsapp_number=phone_number
                        )
                        stats["cached"] += 1

                    stats["instances"].append({
                        "webhook_token": webhook_token[:8] + "...",
                        "instance": instance_name,
                        "clinic_id": clinic_id[:8] + "...",
                        "org_id": org_id[:8] + "..."
                    })

                    logger.info(
                        f"✅ Cached: token={webhook_token[:8]}... → "
                        f"clinic={clinic_id[:8]}... ({clinic_name})"
                    )

                except Exception as e:
                    logger.error(f"Failed to cache integration {integration.get('id')}: {e}")
                    stats["errors"] += 1

            logger.info(
                f"🎉 WhatsApp warmup complete: {stats['tokens_cached']} tokens cached, "
                f"{stats['cached']} instances cached, {stats['errors']} errors"
            )

            return stats

        except Exception as e:
            logger.error(f"❌ WhatsApp cache warmup failed: {e}")
            stats["error"] = str(e)
            return stats

    async def invalidate_instance(self, instance_name: str):
        """Invalidate cache for a specific instance"""
        cache_key = self._make_key(instance_name)
        try:
            self.redis.delete(cache_key)
            logger.info(f"🗑️ Invalidated cache for instance: {instance_name}")
        except Exception as e:
            logger.error(f"Failed to invalidate cache for {instance_name}: {e}")

    async def get_or_fetch_clinic_info(self, instance_name: str) -> Optional[Dict[str, Any]]:
        """
        Get clinic info from cache, or fetch from DB if not cached

        This is a fallback for cache misses during normal operation.

        Returns:
            Dict with clinic info or None
        """
        # Try cache first
        cached = await self.get_clinic_info(instance_name)
        if cached:
            return cached

        # Cache miss - fetch from DB and cache
        logger.info(f"Cache miss for {instance_name}, fetching from DB...")

        try:
            supabase = get_supabase_client()

            # Look up integration by instance name
            result = supabase.schema('healthcare').table('integrations').select(
                'organization_id, config'
            ).eq('type', 'whatsapp').eq('enabled', True).execute()

            # Find matching instance
            for integration in (result.data or []):
                config = integration.get('config', {})
                if config.get('instance_name') == instance_name:
                    org_id = integration.get('organization_id')

                    # Get clinic for this org
                    clinic_result = supabase.table('clinics').select(
                        'id, organization_id, name'
                    ).eq('organization_id', org_id).eq('is_active', True).limit(1).execute()

                    if clinic_result.data and len(clinic_result.data) > 0:
                        clinic = clinic_result.data[0]

                        # Cache it for next time
                        await self.set_clinic_info(
                            instance_name=instance_name,
                            clinic_id=clinic['id'],
                            organization_id=clinic['organization_id'],
                            name=clinic['name'],
                            whatsapp_number=config.get('phone_number')
                        )

                        return {
                            "clinic_id": clinic['id'],
                            "organization_id": clinic['organization_id'],
                            "name": clinic['name'],
                            "whatsapp_number": config.get('phone_number'),
                            "instance_name": instance_name
                        }

            logger.warning(f"No clinic found for instance {instance_name}")
            return None

        except Exception as e:
            logger.error(f"Failed to fetch clinic info for {instance_name}: {e}")
            return None


# Singleton instance
_whatsapp_clinic_cache: Optional[WhatsAppClinicCache] = None


def get_whatsapp_clinic_cache() -> WhatsAppClinicCache:
    """Get or create singleton WhatsAppClinicCache instance"""
    global _whatsapp_clinic_cache
    if _whatsapp_clinic_cache is None:
        _whatsapp_clinic_cache = WhatsAppClinicCache()
    return _whatsapp_clinic_cache
