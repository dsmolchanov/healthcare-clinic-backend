"""Permission checking service with Redis caching."""
import logging
import json
from typing import Optional, List
from supabase import Client

logger = logging.getLogger(__name__)

class PermissionService:
    """Service for checking user permissions with distributed caching."""

    def __init__(self, supabase: Client, redis_manager):
        self.supabase = supabase
        self.redis = redis_manager
        self.cache_ttl = 300  # 5 minutes

    async def get_user_permissions(self, user_id: str, organization_id: str) -> List[str]:
        """Get all permission actions for a user."""
        # Simple cache key using raw Redis API (not namespace-based)
        cache_key = f"permissions:{organization_id}:{user_id}"

        # Try Redis cache first (if available)
        if self.redis:
            try:
                await self.redis.ensure_connected()
                cached = await self.redis.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f"Redis cache read failed: {e}")

        try:
            # Get user's role
            result = self.supabase.schema('core').table('user_organizations')\
                .select('unified_role')\
                .eq('user_id', user_id)\
                .eq('organization_id', organization_id)\
                .eq('is_active', True)\
                .single()\
                .execute()

            if not result.data:
                return []

            role = result.data.get('unified_role')
            if not role:
                return []

            # Super admin gets all permissions
            if role == 'super_admin':
                # Fetch all permission actions
                all_perms = self.supabase.schema('public').table('permissions')\
                    .select('action')\
                    .execute()
                permissions = [p['action'] for p in all_perms.data]
            else:
                # Get role's permissions from matrix (join in single query)
                perms_result = self.supabase.schema('public')\
                    .table('role_permissions')\
                    .select('permissions(action)')\
                    .eq('role', role)\
                    .eq('granted', True)\
                    .execute()

                permissions = [p['permissions']['action'] for p in perms_result.data]

            # Cache in Redis with TTL (if available)
            if self.redis:
                try:
                    await self.redis.ensure_connected()
                    await self.redis.client.setex(cache_key, self.cache_ttl, json.dumps(permissions))
                except Exception as e:
                    logger.warning(f"Redis cache write failed: {e}")

            return permissions

        except Exception as e:
            logger.error(f"Failed to get user permissions: {e}")
            return []

    async def has_permission(self, user_id: str, organization_id: str, action: str) -> bool:
        """Check if user has specific permission."""
        permissions = await self.get_user_permissions(user_id, organization_id)
        return action in permissions

    async def has_any_permission(self, user_id: str, organization_id: str, actions: List[str]) -> bool:
        """Check if user has any of the specified permissions."""
        permissions = await self.get_user_permissions(user_id, organization_id)
        return any(action in permissions for action in actions)

    async def clear_cache(self, user_id: Optional[str] = None, organization_id: Optional[str] = None):
        """Clear permission cache for user or all users."""
        if not self.redis:
            return
        try:
            await self.redis.ensure_connected()

            if user_id and organization_id:
                # Clear specific user cache
                cache_key = f"permissions:{organization_id}:{user_id}"
                await self.redis.client.delete(cache_key)
            elif organization_id:
                # Clear all users in org
                pattern = f"permissions:{organization_id}:*"
                cursor = 0
                while True:
                    cursor, keys = await self.redis.client.scan(cursor, match=pattern, count=100)
                    if keys:
                        await self.redis.client.delete(*keys)
                    if cursor == 0:
                        break
            else:
                # Clear all permission caches
                pattern = "permissions:*"
                cursor = 0
                while True:
                    cursor, keys = await self.redis.client.scan(cursor, match=pattern, count=100)
                    if keys:
                        await self.redis.client.delete(*keys)
                    if cursor == 0:
                        break
        except Exception as e:
            logger.error(f"Failed to clear permission cache: {e}")

# Singleton instance
_permission_service: Optional[PermissionService] = None

def get_permission_service() -> PermissionService:
    """Get permission service instance."""
    global _permission_service
    if _permission_service is None:
        import os
        from supabase import create_client

        # Create Supabase client directly
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

        if not supabase_url or not supabase_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")

        supabase = create_client(supabase_url, supabase_key)

        # Import redis_manager - it may fail but permissions will still work without cache
        try:
            from app.cache.redis_manager import redis_manager
        except Exception as e:
            logger.warning(f"Redis not available for permissions caching: {e}")
            redis_manager = None

        _permission_service = PermissionService(supabase, redis_manager)
    return _permission_service
