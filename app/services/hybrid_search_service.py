"""
Hybrid Multi-Stage Search Service with i18n Support

Implements 4-stage cascading search strategy:
1. Cache Exact Match (language-aware)
2. Full-Text Search (FTS with language-specific configs)
3. Fuzzy Match (trigram similarity)
4. Fallback (ILIKE pattern matching)

Supported entities: services, FAQs, doctors, clinic info
Supported languages: en, es, ru, pt, he
"""

import logging
import time
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

from app.services.clinic_data_cache import ClinicDataCache
from app.services.language_service import LanguageService
from app.utils.text_normalization import normalize_query
from app.database import create_supabase_client

logger = logging.getLogger(__name__)


class SearchStage(Enum):
    """Search stages for telemetry"""
    CACHE_EXACT = "cache_exact"
    CACHE_FUZZY = "cache_fuzzy"
    FTS = "fts"
    TRIGRAM = "trigram"
    FALLBACK = "fallback"
    NO_RESULTS = "no_results"


class EntityType(Enum):
    """Searchable entity types"""
    SERVICE = "service"
    FAQ = "faq"
    DOCTOR = "doctor"
    CLINIC = "clinic"


class HybridSearchService:
    """
    Unified hybrid search service with i18n support
    """

    def __init__(self, clinic_id: str, redis_client, supabase_client=None):
        self.clinic_id = clinic_id
        self.redis = redis_client
        self.supabase = supabase_client or create_supabase_client()

        # Initialize dependencies
        self.cache = ClinicDataCache(redis_client, default_ttl=3600)
        self.language_service = LanguageService(redis_client)

        # Search configuration
        self.search_budget_ms = 500  # Total search budget (can be adjusted)
        self.min_similarity = 0.20  # Trigram similarity threshold

        logger.info(f"Initialized HybridSearchService for clinic {clinic_id}")

    async def search(
        self,
        query: str,
        entity_type: EntityType,
        language: Optional[str] = None,
        limit: int = 10,
        phone_hash: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Universal search method with multi-stage strategy

        Args:
            query: Search query text
            entity_type: Type of entity to search (service, faq, doctor, clinic)
            language: Optional language hint (auto-detected if None)
            limit: Maximum number of results
            phone_hash: Optional phone hash for language caching
            session_id: Optional session ID for FTS search personalization

        Returns:
            Dict with results, metadata, and telemetry
        """
        start_time = time.perf_counter()

        # Language detection
        if not language and phone_hash:
            language = await self.language_service.detect_and_cache(query, phone_hash)
        elif not language:
            try:
                import langdetect
                langdetect.DetectorFactory.seed = 0
                language = langdetect.detect(query)
            except:
                language = 'en'

        # Text normalization
        normalized_query = normalize_query(query)

        logger.info(
            f"🔍 Hybrid search: query='{query}' → normalized='{normalized_query}' "
            f"lang={language} entity={entity_type.value}"
        )

        # Route to entity-specific search
        if entity_type == EntityType.SERVICE:
            results, stage = await self._search_services(normalized_query, language, limit, session_id)
        elif entity_type == EntityType.FAQ:
            results, stage = await self._search_faqs(normalized_query, language, limit)
        elif entity_type == EntityType.DOCTOR:
            results, stage = await self._search_doctors(normalized_query, language, limit)
        else:
            results, stage = [], SearchStage.NO_RESULTS

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            f"✅ Search completed: stage={stage.value} results={len(results)} "
            f"latency={elapsed_ms:.1f}ms"
        )

        return {
            "success": len(results) > 0,
            "results": results,
            "total_count": len(results),
            "search_metadata": {
                "query": query,
                "normalized_query": normalized_query,
                "language": language,
                "entity_type": entity_type.value,
                "search_stage": stage.value,
                "latency_ms": round(elapsed_ms, 2)
            }
        }

    async def _search_services(
        self,
        normalized_query: str,
        language: str,
        limit: int,
        session_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], SearchStage]:
        """
        Multi-stage service search

        Stages:
        1. Cache exact match (language-aware)
        2. Cache fuzzy match (rapidfuzz)
        3. FTS search (language-specific vector)
        4. Trigram match (typo tolerance)
        5. Fallback ILIKE
        """

        # Stage 1: Cache exact match
        cached_services = await self.cache.get_services(self.clinic_id, self.supabase)

        if cached_services:
            # Try language-aware exact match
            exact_matches = self.cache.search_cached_services(
                cached_services,
                normalized_query,
                language
            )

            if exact_matches:
                logger.info(f"✅ Cache exact match: {len(exact_matches)} results")
                return exact_matches[:limit], SearchStage.CACHE_EXACT

            # Stage 2: Fuzzy match on cached data
            fuzzy_matches = self._fuzzy_match_services(
                cached_services,
                normalized_query,
                language
            )

            if fuzzy_matches:
                logger.info(f"🔍 Cache fuzzy match: {len(fuzzy_matches)} results")
                return fuzzy_matches[:limit], SearchStage.CACHE_FUZZY

        # Stage 3: FTS search (database)
        fts_results = await self._fts_search_services(normalized_query, limit, session_id)
        if fts_results:
            logger.info(f"📊 FTS match: {len(fts_results)} results")
            return fts_results, SearchStage.FTS

        # Stage 4: Trigram match (database)
        trigram_results = await self._trigram_search_services(normalized_query, language, limit)
        if trigram_results:
            logger.info(f"🎯 Trigram match: {len(trigram_results)} results")
            return trigram_results, SearchStage.TRIGRAM

        # Stage 5: Fallback ILIKE
        fallback_results = await self._fallback_search_services(normalized_query, limit)
        if fallback_results:
            logger.warning(f"⚠️ Fallback match: {len(fallback_results)} results")
            return fallback_results, SearchStage.FALLBACK

        logger.error(f"❌ No matches found for '{normalized_query}'")
        return [], SearchStage.NO_RESULTS

    def _fuzzy_match_services(
        self,
        cached_services: List[Dict[str, Any]],
        query: str,
        language: str
    ) -> List[Dict[str, Any]]:
        """
        Fuzzy match using rapidfuzz on cached services
        """
        from rapidfuzz import fuzz, process

        # Build searchable text for each service
        service_texts = {}
        for service in cached_services:
            # Prioritize language-specific fields
            if language == 'ru':
                text = service.get('name_ru') or service.get('name', '')
            elif language == 'es':
                text = service.get('name_es') or service.get('name', '')
            elif language == 'pt':
                text = service.get('name_pt') or service.get('name', '')
            elif language == 'he':
                text = service.get('name_he') or service.get('name', '')
            else:
                text = service.get('name_en') or service.get('name', '')

            service_texts[service['id']] = text.lower()

        # Fuzzy match
        matches = process.extract(
            query.lower(),
            service_texts,
            scorer=fuzz.ratio,
            score_cutoff=88,  # 0.88 threshold
            limit=10
        )

        # Map back to service objects
        results = []
        for _, score, service_id in matches:
            for service in cached_services:
                if service['id'] == service_id:
                    service['relevance_score'] = score / 100.0
                    service['match_type'] = 'fuzzy'
                    results.append(service)
                    break

        return results

    async def _fts_search_services(
        self,
        query: str,
        limit: int,
        session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Full-text search using language-specific vectors

        Note: Updated to use new function signature without p_language parameter.
        The database function now uses p_session_id for personalization instead.
        """
        try:
            # Use new function signature (removed p_language, added p_session_id)
            response = self.supabase.rpc(
                'search_services_v1',
                {
                    'p_clinic_id': self.clinic_id,
                    'p_limit': limit,
                    'p_min_score': 0.01,
                    'p_query': query,
                    'p_session_id': session_id or ''  # Use empty string if no session_id
                }
            ).execute()

            return response.data if response.data else []
        except Exception as e:
            logger.error(f"FTS search failed: {e}")
            return []

    async def _trigram_search_services(
        self,
        query: str,
        language: str,
        limit: int
    ) -> List[Dict[str, Any]]:
        """
        Trigram similarity search for typo tolerance
        """
        try:
            # Determine which name field to search based on language
            name_field = f"name_{language}" if language in ['ru', 'es', 'pt', 'he'] else "name"

            # Direct query with trigram similarity
            result = self.supabase.schema('healthcare').table('services').select(
                '*'
            ).eq('clinic_id', self.clinic_id).eq('active', True).execute()

            # Filter by similarity threshold (done in Python for simplicity)
            from rapidfuzz import fuzz
            matches = []
            for service in result.data or []:
                name_value = service.get(name_field) or service.get('name', '')
                similarity = fuzz.ratio(query.lower(), name_value.lower()) / 100.0

                if similarity >= self.min_similarity:
                    service['relevance_score'] = similarity
                    service['match_type'] = 'trigram'
                    matches.append(service)

            # Sort by similarity
            matches.sort(key=lambda x: x['relevance_score'], reverse=True)
            return matches[:limit]

        except Exception as e:
            logger.error(f"Trigram search failed: {e}")
            return []

    async def _fallback_search_services(
        self,
        query: str,
        limit: int
    ) -> List[Dict[str, Any]]:
        """
        Fallback ILIKE search (least efficient, last resort)
        """
        try:
            result = self.supabase.schema('healthcare').table('services').select(
                '*'
            ).eq('clinic_id', self.clinic_id).eq('active', True).or_(
                f'name.ilike.%{query}%,'
                f'name_ru.ilike.%{query}%,'
                f'name_en.ilike.%{query}%,'
                f'name_es.ilike.%{query}%,'
                f'description.ilike.%{query}%'
            ).limit(limit).execute()

            for service in result.data or []:
                service['relevance_score'] = 0.0
                service['match_type'] = 'fallback'

            return result.data or []
        except Exception as e:
            logger.error(f"Fallback search failed: {e}")
            return []

    async def _search_faqs(
        self,
        normalized_query: str,
        language: str,
        limit: int
    ) -> Tuple[List[Dict[str, Any]], SearchStage]:
        """
        FAQ search (already well-implemented, just wrap it)
        """
        try:
            response = self.supabase.rpc(
                'search_faqs',
                {
                    'p_clinic_id': self.clinic_id,
                    'p_query': normalized_query,
                    'p_language': self._map_language_to_fts_config(language),
                    'p_limit': limit,
                    'p_min_score': 0.1
                }
            ).execute()

            results = response.data if response.data else []
            stage = SearchStage.FTS if results else SearchStage.NO_RESULTS
            return results, stage
        except Exception as e:
            logger.error(f"FAQ search failed: {e}")
            return [], SearchStage.NO_RESULTS

    async def _search_doctors(
        self,
        normalized_query: str,
        language: str,
        limit: int
    ) -> Tuple[List[Dict[str, Any]], SearchStage]:
        """
        Doctor search (simple cache + fuzzy match)
        """
        cached_doctors = await self.cache.get_doctors(self.clinic_id, self.supabase)

        if not cached_doctors:
            return [], SearchStage.NO_RESULTS

        # Simple substring match on name and specialization
        matches = []
        query_lower = normalized_query.lower()

        for doctor in cached_doctors:
            name = f"{doctor.get('first_name', '')} {doctor.get('last_name', '')}".lower()
            specialization = doctor.get('specialization', '').lower()

            if query_lower in name or query_lower in specialization:
                doctor['match_type'] = 'substring'
                matches.append(doctor)

        stage = SearchStage.CACHE_EXACT if matches else SearchStage.NO_RESULTS
        return matches[:limit], stage

    def _map_language_to_fts_config(self, language: str) -> str:
        """Map ISO 639-1 to PostgreSQL FTS config"""
        mapping = {
            'en': 'english',
            'es': 'spanish',
            'ru': 'russian',
            'pt': 'portuguese',
            'he': 'english'  # Fallback (no Hebrew FTS config)
        }
        return mapping.get(language, 'english')
