"""
FAQ Query Tool - Fast full-text search for frequently asked questions
Uses Supabase FTS instead of vector search for instant, structured responses
"""

import os
import logging
from typing import Optional, List, Dict, Any
from supabase import create_client, Client

logger = logging.getLogger(__name__)


class FAQQueryTool:
    """Tool for querying FAQs using Supabase Full-Text Search"""

    # Language code mapping (ISO 639-1 to PostgreSQL FTS config)
    LANGUAGE_MAP = {
        'en': 'english',
        'es': 'spanish',
        'ru': 'russian',
        'pt': 'portuguese',
        'he': 'english',  # Fallback (PostgreSQL doesn't have Hebrew config)
    }

    # Category keywords for auto-detection
    CATEGORY_KEYWORDS = {
        'hours': ['hours', 'open', 'close', 'schedule', 'horario', 'abierto'],
        'location': ['location', 'address', 'where', 'ubicación', 'dirección', 'parking', 'estacionamiento'],
        'insurance': ['insurance', 'seguro', 'coverage', 'cobertura', 'accept'],
        'pricing': ['price', 'cost', 'fee', 'precio', 'costo', 'tarifa', 'how much'],
        'services': ['service', 'treatment', 'procedure', 'servicio', 'tratamiento'],
        'policies': ['policy', 'rule', 'cancel', 'política', 'regla', 'cancelación'],
    }

    def __init__(self, clinic_id: str):
        """
        Initialize FAQ query tool

        Args:
            clinic_id: UUID of the clinic
        """
        self.clinic_id = clinic_id

        # Initialize Supabase client (matches PriceQueryTool pattern)
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

        if not supabase_url or not supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY/SUPABASE_ANON_KEY must be set")

        self.client: Client = create_client(supabase_url, supabase_key)

    async def search_faqs(
        self,
        query: str,
        language: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.1
    ) -> List[Dict[str, Any]]:
        """
        Search FAQs using full-text search

        Args:
            query: User's question
            language: Language code (en, es, ru) - defaults to 'en'
            category: Optional category filter
            limit: Maximum results to return
            min_score: Minimum relevance score (0.0-1.0)

        Returns:
            List of matching FAQs with relevance scores
        """
        try:
            # Default language to English if not provided
            if not language:
                language = 'en'

            # Map language code to PostgreSQL config
            fts_language = self.LANGUAGE_MAP.get(language, 'english')

            # Auto-detect category from query if not provided
            if not category:
                category = self._detect_category(query.lower())

            # Call RPC function
            response = self.client.rpc(
                'search_faqs',
                {
                    'p_clinic_id': self.clinic_id,
                    'p_query': query,
                    'p_language': fts_language,
                    'p_category': category,
                    'p_limit': limit,
                    'p_min_score': min_score
                }
            ).execute()

            if not response.data:
                logger.info(f"No FAQ results for query: {query} (lang={fts_language}, cat={category})")
                return []

            # Track view for top result (fire-and-forget)
            if response.data:
                self._increment_view(response.data[0]['id'])

            logger.info(f"Found {len(response.data)} FAQs for query: {query}")
            return response.data

        except Exception as e:
            logger.error(f"FAQ search failed: {e}", exc_info=True)
            return []

    async def get_by_category(
        self,
        category: str,
        language: str = 'english',
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get all FAQs in a specific category"""
        try:
            response = self.client.rpc(
                'get_faqs_by_category',
                {
                    'p_clinic_id': self.clinic_id,
                    'p_category': category,
                    'p_language': language,
                    'p_limit': limit
                }
            ).execute()

            return response.data if response.data else []

        except Exception as e:
            logger.error(f"Failed to get FAQs by category: {e}", exc_info=True)
            return []

    async def get_featured_faqs(
        self,
        language: str = 'english',
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Get featured/popular FAQs for quick access"""
        try:
            response = self.client.rpc(
                'get_featured_faqs',
                {
                    'p_clinic_id': self.clinic_id,
                    'p_language': language,
                    'p_limit': limit
                }
            ).execute()

            return response.data if response.data else []

        except Exception as e:
            logger.error(f"Failed to get featured FAQs: {e}", exc_info=True)
            return []

    async def record_feedback(self, faq_id: int, is_helpful: bool):
        """Record user feedback on FAQ helpfulness"""
        try:
            self.client.rpc(
                'record_faq_feedback',
                {
                    'p_faq_id': faq_id,
                    'p_is_helpful': is_helpful
                }
            ).execute()
            logger.debug(f"Recorded feedback for FAQ {faq_id}: {'helpful' if is_helpful else 'not helpful'}")
        except Exception as e:
            logger.debug(f"Failed to record feedback: {e}")

    def _detect_category(self, query_lower: str) -> Optional[str]:
        """Auto-detect category from query keywords"""
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            if any(kw in query_lower for kw in keywords):
                return category
        return None

    def _increment_view(self, faq_id: int):
        """Increment view count for analytics (fire-and-forget, non-blocking)"""
        try:
            self.client.rpc('increment_faq_view', {'p_faq_id': faq_id}).execute()
        except Exception as e:
            # Non-critical - log but don't fail the search
            logger.debug(f"Failed to increment FAQ view: {e}")


# ============================================================
# LLM Tool Function (for orchestrator integration)
# ============================================================

async def query_faqs(
    clinic_id: str,
    query: str,
    language: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 3
) -> str:
    """
    Query FAQs for a clinic - designed for LLM tool calling

    Args:
        clinic_id: UUID of the clinic
        query: User's question
        language: Language code (en, es, ru) - auto-detected if None
        category: Optional category filter
        limit: Maximum number of results (default 3)

    Returns:
        Formatted string with FAQ answers suitable for LLM context
    """
    tool = FAQQueryTool(clinic_id)
    faqs = await tool.search_faqs(
        query=query,
        language=language,
        category=category,
        limit=limit
    )

    if not faqs:
        return (
            f"No FAQ found matching '{query}'. "
            "You may need to check the knowledge base or escalate to a human agent."
        )

    # Format as readable text for LLM
    result_lines = [f"Found {len(faqs)} relevant FAQ(s):\n"]

    for i, faq in enumerate(faqs, 1):
        score_pct = faq.get('relevance_score', 0) * 10  # Normalize to 0-100%
        featured_badge = " ⭐" if faq.get('is_featured') else ""

        result_lines.append(f"{i}. **Q: {faq['question']}**{featured_badge}")
        result_lines.append(f"   **A:** {faq['answer']}")

        if faq.get('category'):
            result_lines.append(f"   📁 Category: {faq['category'].title()}")

        if faq.get('tags'):
            result_lines.append(f"   🏷️  Tags: {', '.join(faq['tags'])}")

        result_lines.append(f"   📊 Relevance: {score_pct:.0f}% | Views: {faq.get('view_count', 0)}")
        result_lines.append("")  # Empty line between FAQs

    # Add context for LLM
    result_lines.append(
        "\n💡 **Instructions:** Use this information to answer the user's question. "
        "Cite the FAQ number if you use it. If the answer is incomplete, "
        "you may search the knowledge base or ask clarifying questions."
    )

    return "\n".join(result_lines)


# Export for tool registry
__all__ = ['FAQQueryTool', 'query_faqs']
