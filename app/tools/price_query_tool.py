"""
Price Query Tool - Get service prices from the healthcare.services table

This tool allows the orchestrator to query service prices, descriptions, and details
for the clinic to provide accurate pricing information to patients.

Uses multi-layer resilient search:
- Layer 0: Exact alias matching (zero-miss for key services)
- Layer 1: Dual-language FTS (Russian + English)
- Layer 2: FTS with OR relaxation and prefix matching
- Layer 3: Trigram fuzzy matching (typo tolerance)

CACHING STRATEGY:
- All services are preloaded into Redis on startup (see app/startup_warmup.py)
- Searches are performed against cached data when possible
- Falls back to database RPC for complex searches
"""

import os
import logging
import uuid
import time
from typing import Optional, List, Dict, Any
from supabase import create_client, Client
from supabase.client import ClientOptions
from postgrest.exceptions import APIError
from app.utils.text_normalization import (
    normalize_query,
    expand_synonyms,
    format_price_reply,
    quick_reply
)

logger = logging.getLogger(__name__)

# Latency budget for price queries (800ms)
PRICE_QUERY_BUDGET_MS = 800


class PriceQueryTool:
    """Tool for querying service prices from the database"""

    def __init__(self, clinic_id: str, redis_client=None):
        """
        Initialize the price query tool

        Args:
            clinic_id: UUID of the clinic to query services for
            redis_client: Optional Redis client for caching (if None, caching disabled)
        """
        self.clinic_id = clinic_id
        self.redis_client = redis_client

        # Initialize Supabase client
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

        if not supabase_url or not supabase_key:
            raise ValueError("Supabase credentials not configured")

        api_options = ClientOptions(
            schema='public',
            auto_refresh_token=True,
            persist_session=False
        )
        healthcare_options = ClientOptions(
            schema='healthcare',
            auto_refresh_token=True,
            persist_session=False
        )

        self.api_client: Client = create_client(supabase_url, supabase_key, options=api_options)
        self.healthcare_client: Client = create_client(supabase_url, supabase_key, options=healthcare_options)

        # Initialize cache manager if Redis available
        self.cache = None
        if redis_client:
            from app.services.clinic_data_cache import ClinicDataCache
            self.cache = ClinicDataCache(redis_client, default_ttl=3600)

    async def _search_cached_services(
        self,
        query: str,
        category: Optional[str],
        limit: int
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Try to search cached services using simple text matching

        Returns None if cache unavailable or query too complex for simple matching
        """
        if not self.cache:
            return None

        try:
            # Get all services from cache
            services = await self.cache.get_services(self.clinic_id, self.healthcare_client)
            if not services:
                return None

            # Simple case-insensitive search in name and description
            query_lower = query.lower()
            matches = []

            for service in services:
                name = (service.get('name') or '').lower()
                desc = (service.get('description') or '').lower()
                service_category = (service.get('category') or '').lower()

                # Apply category filter first
                if category and category.lower() not in service_category:
                    continue

                # Check if query matches name or description
                if query_lower in name or query_lower in desc:
                    matches.append({
                        "id": service["id"],
                        "name": service["name"],
                        "description": service.get("description", ""),
                        "price": float(service["base_price"]) if service.get("base_price") else None,
                        "currency": service.get("currency", "USD"),
                        "category": service.get("category", ""),
                        "duration_minutes": service.get("duration_minutes", 30),
                        "code": service.get("code", ""),
                        "relevance_score": 1.0,  # Simple match, no scoring
                        "search_stage": "cached"
                    })

            if matches:
                logger.info(f"✅ Cache HIT: Found {len(matches)} services for '{query}' in cache")
                return matches[:limit]
            else:
                logger.debug(f"❌ Cache MISS: No cached matches for '{query}', falling back to RPC")
                return None

        except Exception as e:
            logger.warning(f"Cache search failed: {e}, falling back to RPC")
            return None

    async def get_services_by_query(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 10,
        session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Query services using multi-layer search with caching

        Search strategy:
        1. Try cache for simple queries (fast path)
        2. Fall back to resilient RPC search for complex queries
        3. Final fallback to ILIKE search

        Args:
            query: Search term to match against service name or description
            category: Filter by service category (applied after main search)
            limit: Maximum number of results to return (default: 10)
            session_id: Optional session ID for telemetry tracking

        Returns:
            List of service dictionaries with name, description, price, category, duration, search_stage
        """
        try:
            # Try cache first for simple queries
            if query:
                cached_results = await self._search_cached_services(query, category, limit)
                if cached_results is not None:
                    return cached_results

                # Cache miss or complex query - use RPC
                return await self._resilient_search(query, category, limit, session_id)

            # No query - list all or by category
            return await self._list_by_category(category, limit)

        except Exception as e:
            logger.error(f"Error querying services: {e}")
            # Final fallback to old ILIKE search
            return await self._fallback_ilike_search(query, category, limit)

    async def _resilient_search(
        self,
        query: str,
        category: Optional[str],
        limit: int,
        session_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """
        Use the multilingual resilient search with synonym expansion

        Strategy:
        1. Normalize query
        2. Expand synonyms
        3. Try multilingual search for each synonym until hit
        4. Union results and de-duplicate
        """
        try:
            t0 = time.perf_counter()

            # Generate session ID if not provided
            if not session_id:
                session_id = str(uuid.uuid4())
            else:
                # Ensure session_id is a valid UUID for RPC payloads
                try:
                    session_id = str(uuid.UUID(str(session_id)))
                except ValueError:
                    session_id = str(uuid.uuid4())

            # Normalize and expand query
            normalized = normalize_query(query)
            synonyms = expand_synonyms(query)

            logger.info(
                f"🔍 Query expansion: '{query}' → normalized: '{normalized}' → "
                f"synonyms: {synonyms[:3]}..." if len(synonyms) > 3 else f"synonyms: {synonyms}"
            )

            all_results = {}  # service_id → service (de-dup)

            # Try multilingual search for each synonym
            for synonym in synonyms[:5]:  # Limit to 5 synonyms to avoid excessive queries
                elapsed_ms = (time.perf_counter() - t0) * 1000
                if elapsed_ms > PRICE_QUERY_BUDGET_MS:
                    logger.warning(f"⏱️ Search budget exceeded ({elapsed_ms:.0f}ms), stopping synonym expansion")
                    break

                payload = {
                    'p_clinic_id': self.clinic_id,
                    'p_query': synonym,
                    'p_limit': limit,
                    'p_min_score': 0.01,
                    'p_session_id': session_id
                }

                try:
                    response = self.api_client.rpc(
                        'search_services_v1',
                        payload
                    ).execute()
                except APIError as api_err:
                    logger.warning(
                        "Primary service search RPC failed for synonym '%s': %s. Falling back to legacy multilingual search.",
                        synonym,
                        getattr(api_err, 'message', api_err)
                    )
                    response = self.healthcare_client.rpc(
                        'search_services_multilingual',
                        payload
                    ).execute()
                except Exception as syn_err:
                    logger.warning(f"Synonym search failed for '{synonym}': {syn_err}")
                    continue

                if response.data:
                    for service in response.data:
                        if service["id"] not in all_results:
                            all_results[service["id"]] = service

                    logger.info(
                        f"✅ Found {len(response.data)} results for synonym '{synonym}' "
                        f"(stage: {response.data[0].get('search_stage', 'unknown')})"
                    )
                    # Stop after first successful match
                    break
                else:
                    logger.debug(f"❌ No results for synonym '{synonym}'")

            # Convert to list and apply category filter
            services = []
            for service in all_results.values():
                # Apply category filter if specified
                if category and category.lower() not in service.get("category", "").lower():
                    continue

                services.append({
                    "id": service["id"],
                    "name": service["name"],
                    "description": service.get("description", ""),
                    "price": float(service["base_price"]) if service.get("base_price") else None,
                    "currency": service.get("currency", "USD"),
                    "category": service.get("category", ""),
                    "duration_minutes": service.get("duration_minutes", 30),
                    "code": service.get("code", ""),
                    "relevance_score": service.get("relevance_score", 0.0),
                    "search_stage": service.get("search_stage", "unknown")
                })

            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.info(
                f"🎯 Multilingual search completed in {elapsed_ms:.0f}ms: "
                f"found {len(services)} unique services for '{query}' "
                f"(stage: {services[0]['search_stage'] if services else 'none'})"
            )

            return services

        except Exception as e:
            logger.error(f"Resilient search RPC failed: {e}")
            raise

    async def _list_by_category(
        self,
        category: Optional[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        """
        Simple category listing (no search query)
        """
        try:
            query_builder = (
                self.healthcare_client.table("services")
                .select("id, name, description, base_price, category, duration_minutes, currency, code")
                .eq("clinic_id", self.clinic_id)
                .eq("is_active", True)
                .order("category", desc=False)
                .order("name", desc=False)
                .limit(limit)
            )

            if category:
                query_builder = query_builder.ilike("category", f"%{category}%")

            response = query_builder.execute()

            if not response.data:
                return []

            services = []
            for service in response.data:
                services.append({
                    "id": service["id"],
                    "name": service["name"],
                    "description": service.get("description", ""),
                    "price": float(service["base_price"]) if service.get("base_price") else None,
                    "currency": service.get("currency", "USD"),
                    "category": service.get("category", ""),
                    "duration_minutes": service.get("duration_minutes", 30),
                    "code": service.get("code", ""),
                    "search_stage": "category_list"
                })

            return services

        except Exception as e:
            logger.error(f"Category listing failed: {e}")
            return []

    async def _fallback_ilike_search(
        self,
        query: Optional[str],
        category: Optional[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        """
        Fallback to old ILIKE search if RPC fails
        """
        try:
            query_builder = (
                self.healthcare_client.table("services")
                .select("id, name, description, base_price, category, duration_minutes, currency, code")
                .eq("clinic_id", self.clinic_id)
                .eq("is_active", True)
                .order("name", desc=False)
                .limit(limit)
            )

            if category:
                query_builder = query_builder.ilike("category", f"%{category}%")

            if query:
                query_builder = query_builder.ilike("name", f"%{query}%")

            response = query_builder.execute()

            # If no results with name search, try description
            if not response.data and query:
                query_builder = (
                    self.healthcare_client.table("services")
                    .select("id, name, description, base_price, category, duration_minutes, currency, code")
                    .eq("clinic_id", self.clinic_id)
                    .eq("is_active", True)
                    .ilike("description", f"%{query}%")
                    .order("name", desc=False)
                    .limit(limit)
                )
                if category:
                    query_builder = query_builder.ilike("category", f"%{category}%")
                response = query_builder.execute()

            if not response.data:
                return []

            services = []
            for service in response.data:
                services.append({
                    "id": service["id"],
                    "name": service["name"],
                    "description": service.get("description", ""),
                    "price": float(service["base_price"]) if service.get("base_price") else None,
                    "currency": service.get("currency", "USD"),
                    "category": service.get("category", ""),
                    "duration_minutes": service.get("duration_minutes", 30),
                    "code": service.get("code", ""),
                    "search_stage": "fallback_ilike"
                })

            return services

        except Exception as e:
            logger.error(f"Fallback ILIKE search failed: {e}")
            return []

    async def get_all_categories(self) -> List[str]:
        """
        Get all unique service categories for the clinic

        Returns:
            List of category names
        """
        try:
            response = (
                self.healthcare_client.table("services")
                .select("category")
                .eq("clinic_id", self.clinic_id)
                .eq("active", True)
                .execute()
            )

            if not response.data:
                return []

            # Extract unique categories
            categories = list(set([s["category"] for s in response.data if s.get("category")]))
            categories.sort()

            return categories

        except Exception as e:
            logger.error(f"Error getting categories: {e}")
            return []

    async def get_service_by_name(self, service_name: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific service by exact name match

        Args:
            service_name: Exact service name to search for

        Returns:
            Service dictionary or None if not found
        """
        try:
            response = (
                self.healthcare_client.table("services")
                .select("id, name, description, base_price, category, duration_minutes, currency, code")
                .eq("clinic_id", self.clinic_id)
                .eq("active", True)
                .ilike("name", service_name)
                .limit(1)
                .execute()
            )

            if not response.data:
                return None

            service = response.data[0]
            return {
                "id": service["id"],
                "name": service["name"],
                "description": service.get("description", ""),
                "price": float(service["base_price"]) if service.get("base_price") else None,
                "currency": service.get("currency", "USD"),
                "category": service.get("category", ""),
                "duration_minutes": service.get("duration_minutes", 30),
                "code": service.get("code", "")
            }

        except Exception as e:
            logger.error(f"Error getting service by name: {e}")
            return None

    async def get_formatted_price_response(
        self,
        query: str,
        language: str = "ru",
        session_id: Optional[str] = None
    ) -> str:
        """
        Get deterministic price response WITHOUT LLM

        This bypasses the LLM entirely and returns a pre-formatted response
        for maximum speed and consistency.

        Args:
            query: Search query (e.g., "смолы?", "пломба", "composite")
            language: Language code (ru, en)
            session_id: Optional session ID for tracking

        Returns:
            Formatted price response with CTA
        """
        t0 = time.perf_counter()

        try:
            # Search with budget
            services = await self.get_services_by_query(
                query=query,
                limit=1,
                session_id=session_id
            )

            elapsed_ms = (time.perf_counter() - t0) * 1000

            # Budget exceeded or no results
            if elapsed_ms > PRICE_QUERY_BUDGET_MS or not services:
                logger.warning(
                    f"⏱️ Price query budget exceeded or no results "
                    f"({elapsed_ms:.0f}ms) for '{query}', returning quick reply"
                )
                return quick_reply(language)

            # Format deterministic response
            service = services[0]
            response = format_price_reply(
                service_name=service["name"],
                price=service["price"] or 0,
                currency=service.get("currency", "USD"),
                language=language,
                unit="per surface"  # Default unit, can be customized
            )

            logger.info(
                f"💰 Price response generated in {elapsed_ms:.0f}ms: "
                f"'{query}' → {service['name']} (${service['price']}) "
                f"[{service['search_stage']}]"
            )

            return response

        except Exception as e:
            logger.error(f"Error generating price response: {e}")
            return quick_reply(language)


# Tool function for LLM orchestrator
async def query_service_prices(
    clinic_id: str,
    query: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    redis_client=None  # Optional Redis client for caching
) -> str:
    """
    Query service prices for a clinic

    This function is designed to be called by the LLM orchestrator as a tool.

    Args:
        clinic_id: UUID of the clinic
        query: Search term (e.g., "filling", "cleaning", "whitening")
        category: Service category filter
        limit: Maximum number of results
        redis_client: Optional Redis client for caching (significantly faster)

    Returns:
        Formatted string with service information
    """
    tool = PriceQueryTool(clinic_id, redis_client=redis_client)
    services = await tool.get_services_by_query(query=query, category=category, limit=limit)

    if not services:
        return f"No services found matching '{query or 'any'}' in category '{category or 'any'}'"

    # Format as readable text
    result_lines = [f"Found {len(services)} service(s):\n"]

    for i, service in enumerate(services, 1):
        price_str = f"{service['price']:.2f} {service['currency']}" if service['price'] else "Price not set"
        duration_str = f"{service['duration_minutes']} min" if service['duration_minutes'] else ""

        result_lines.append(f"{i}. **{service['name']}** - {price_str}")
        if service['description']:
            result_lines.append(f"   Description: {service['description']}")
        if service['category']:
            result_lines.append(f"   Category: {service['category']}")
        if duration_str:
            result_lines.append(f"   Duration: {duration_str}")
        result_lines.append("")  # Empty line between services

    return "\n".join(result_lines)
