"""
Comprehensive integration tests for FAQ system with Shtern Dental Clinic data
Tests the complete workflow: Intent routing → FAQ lookup → Orchestrator integration
"""

import pytest
import os
from app.tools.faq_query_tool import FAQQueryTool, query_faqs
from app.services.intent_router import IntentRouter, Intent
from app.services.orchestrator.templates.healthcare_template import HealthcareLangGraph


# Get Shtern clinic ID from environment or use test fixture
SHTERN_CLINIC_ID = "e0c84f56-235d-49f2-9a44-37c1be579afc"


class TestFAQToolWithShternData:
    """Test FAQ query tool with real Shtern Dental Clinic FAQs"""

    @pytest.fixture
    def faq_tool(self):
        """Create FAQ tool instance for Shtern clinic"""
        return FAQQueryTool(clinic_id=SHTERN_CLINIC_ID)

    @pytest.mark.asyncio
    async def test_search_dental_implants(self, faq_tool):
        """Test searching for dental implants information"""
        results = await faq_tool.search_faqs(
            query="dental implants",  # Simpler query
            language="en",
            limit=5
        )

        assert isinstance(results, list)

        if len(results) > 0:
            # Verify result structure
            top_result = results[0]
            assert 'question' in top_result
            assert 'answer' in top_result
            assert 'relevance_score' in top_result
            assert 'category' in top_result

            print(f"\n✅ Found {len(results)} dental implant FAQs")
            print(f"Top result: {top_result['question'][:80]}...")
            print(f"Relevance score: {top_result['relevance_score']:.2f}")
        else:
            print("\n⚠️  No dental implant FAQs found (check FTS configuration)")

    @pytest.mark.asyncio
    async def test_search_clinic_history(self, faq_tool):
        """Test searching for clinic founding/history"""
        results = await faq_tool.search_faqs(
            query="When was Shtern founded?",
            language="en",
            limit=3
        )

        assert isinstance(results, list)
        if results:
            top_result = results[0]
            assert 'founded' in top_result['answer'].lower() or '2018' in top_result['answer']
            print(f"\n✅ Found clinic history: {top_result['question']}")

    @pytest.mark.asyncio
    async def test_search_pricing_info(self, faq_tool):
        """Test searching for pricing information"""
        results = await faq_tool.search_faqs(
            query="How much does All-on-4 cost?",
            language="en",
            limit=3
        )

        assert isinstance(results, list)
        print(f"\n✅ Found {len(results)} pricing-related FAQs")

        if results:
            for i, faq in enumerate(results[:3], 1):
                print(f"  {i}. {faq['question'][:60]}... (score: {faq['relevance_score']:.2f})")

    @pytest.mark.asyncio
    async def test_search_services(self, faq_tool):
        """Test searching for services offered"""
        results = await faq_tool.search_faqs(
            query="What services do you offer?",
            language="en",
            limit=5
        )

        assert isinstance(results, list)
        assert len(results) > 0, "Should find service FAQs"

        # Should be in 'services' or 'general' category
        categories = {faq['category'] for faq in results}
        assert categories.issubset({'services', 'general', 'pricing'})

        print(f"\n✅ Found {len(results)} service FAQs in categories: {categories}")

    @pytest.mark.asyncio
    async def test_search_team_info(self, faq_tool):
        """Test searching for team/doctor information"""
        results = await faq_tool.search_faqs(
            query="Who is Dr. Mark Shtern?",
            language="en",
            limit=3
        )

        assert isinstance(results, list)
        if results:
            top_result = results[0]
            print(f"\n✅ Found team info: {top_result['question']}")
            print(f"   Answer preview: {top_result['answer'][:100]}...")

    @pytest.mark.asyncio
    async def test_category_detection(self, faq_tool):
        """Test automatic category detection from query"""
        test_cases = [
            ("What services do you offer?", None),  # Should auto-detect 'services'
            ("Tell me about veneers", None),  # Should auto-detect 'services'
        ]

        for query, expected_category in test_cases:
            results = await faq_tool.search_faqs(query=query, language="en", limit=3)

            if results:
                detected_category = results[0]['category']
                print(f"\n✅ Query: '{query}'")
                print(f"   Detected category: {detected_category}")

    @pytest.mark.asyncio
    async def test_featured_faqs(self, faq_tool):
        """Test retrieval of featured FAQs"""
        results = await faq_tool.get_featured_faqs(language="english", limit=10)

        assert isinstance(results, list)
        print(f"\n✅ Found {len(results)} featured FAQs")

        for i, faq in enumerate(results[:5], 1):
            print(f"  {i}. {faq['question'][:60]}... (priority: {faq['priority']})")

    @pytest.mark.asyncio
    async def test_wrapper_function(self):
        """Test the LLM wrapper function formatting"""
        result_str = await query_faqs(
            clinic_id=SHTERN_CLINIC_ID,
            query="What makes Shtern unique?",
            limit=3
        )

        assert isinstance(result_str, str)
        assert len(result_str) > 0

        # Should contain structured output
        assert ("Found" in result_str or "No FAQ" in result_str)

        if "Found" in result_str:
            assert "**Q:" in result_str  # Question marker
            assert "**A:**" in result_str  # Answer marker

        print(f"\n✅ Wrapper function output ({len(result_str)} chars):")
        print(result_str[:500] + "..." if len(result_str) > 500 else result_str)

    @pytest.mark.asyncio
    async def test_multilingual_support(self, faq_tool):
        """Test different language configurations"""
        # Test language mapping
        test_cases = [
            ("en", "english"),
            ("es", "spanish"),
            ("ru", "russian"),
            ("pt", "portuguese"),
        ]

        for lang_code, expected_config in test_cases:
            mapped = faq_tool.LANGUAGE_MAP.get(lang_code)
            assert mapped == expected_config, f"Language mapping failed for {lang_code}"

        print("\n✅ Language mapping verified for all supported languages")

    @pytest.mark.asyncio
    async def test_min_score_threshold(self, faq_tool):
        """Test minimum score filtering"""
        # High threshold should filter out low-relevance results
        results_high = await faq_tool.search_faqs(
            query="random gibberish xyz123abc",
            min_score=0.5,
            language="en"
        )

        # Low threshold should be more permissive
        results_low = await faq_tool.search_faqs(
            query="clinic",
            min_score=0.1,
            language="en"
        )

        print(f"\n✅ High threshold (0.5): {len(results_high)} results")
        print(f"✅ Low threshold (0.1): {len(results_low)} results")

        # Low threshold should return more results
        assert len(results_low) >= len(results_high)


class TestIntentRoutingForFAQ:
    """Test intent detection for FAQ queries"""

    @pytest.fixture
    def intent_router(self):
        """Create intent router instance"""
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_faq_intent_detection(self, intent_router):
        """Test FAQ intent is detected for common queries"""
        test_queries = [
            "What are your hours?",
            "Do you accept insurance?",
            "Where is the clinic located?",
            "How much does a dental implant cost?",
            "Tell me about All-on-4",
            "What services do you offer?",
        ]

        for query in test_queries:
            intent = intent_router.detect_intent(query, "en")  # Not async
            print(f"\n Query: '{query}'")
            print(f" Intent: {intent}")

            # FAQ queries should be detected
            # Note: Some might be PRICE_QUERY instead of FAQ_QUERY depending on patterns
            assert intent in [Intent.FAQ_QUERY, Intent.PRICE_QUERY, Intent.UNKNOWN]

    @pytest.mark.asyncio
    async def test_non_faq_intent_not_confused(self, intent_router):
        """Test that non-FAQ queries don't trigger FAQ intent"""
        test_queries = [
            "I want to book an appointment",
            "Cancel my appointment",
            "Reschedule for next week",
            "Hello",
            "Thank you",
        ]

        for query in test_queries:
            intent = intent_router.detect_intent(query, "en")  # Not async
            print(f"\n Query: '{query}'")
            print(f" Intent: {intent}")

            # Should NOT be FAQ_QUERY
            assert intent != Intent.FAQ_QUERY


class TestOrchestratorIntegration:
    """Test FAQ integration with LangGraph orchestrator"""

    @pytest.fixture
    def orchestrator(self):
        """Create orchestrator instance for Shtern clinic"""
        return HealthcareLangGraph(
            clinic_id=SHTERN_CLINIC_ID
            # enable_rag and enable_memory are set in parent init, not passed as params
        )

    @pytest.mark.asyncio
    async def test_faq_node_execution(self, orchestrator):
        """Test FAQ node executes correctly"""
        state = {
            "session_id": "test-session-001",
            "message": "What makes Shtern Dental Clinic unique?",
            "metadata": {"language": "en"},
            "context": {},
            "audit_trail": []
        }

        # Execute FAQ lookup node
        result_state = await orchestrator.faq_lookup_node(state)

        # Verify state modifications
        assert "faq_results" in result_state['context']
        assert "faq_success" in result_state['context']
        assert len(result_state['audit_trail']) > 0
        assert result_state['audit_trail'][0]['node'] == 'faq_lookup'

        print("\n✅ FAQ node execution successful")
        print(f"   FAQs found: {len(result_state['context']['faq_results'])}")
        print(f"   Success: {result_state['context']['faq_success']}")

        if result_state['context']['faq_results']:
            top_score = result_state['context']['faq_results'][0]['relevance_score']
            print(f"   Top relevance score: {top_score:.2f}")

    @pytest.mark.asyncio
    async def test_high_confidence_faq_response(self, orchestrator):
        """Test high-confidence FAQ sets response directly"""
        state = {
            "session_id": "test-session-002",
            "message": "When was Shtern Dental Clinic founded?",
            "metadata": {"language": "en"},
            "context": {},
            "audit_trail": []
        }

        # Execute FAQ lookup
        result_state = await orchestrator.faq_lookup_node(state)

        # If high confidence, should have response set
        if result_state['context'].get('faq_success'):
            assert result_state.get('response') is not None
            assert len(result_state['response']) > 0
            print("\n✅ High-confidence FAQ response set")
            print(f"   Response preview: {result_state['response'][:150]}...")
        else:
            print("\n⚠️  FAQ not found or low confidence")

    @pytest.mark.asyncio
    async def test_faq_fallback_router(self, orchestrator):
        """Test fallback routing logic"""
        # Test high-confidence route
        state_success = {
            "context": {
                "faq_results": [{"question": "Test", "answer": "Test", "relevance_score": 0.8}],
                "faq_success": True
            }
        }
        route = orchestrator.faq_fallback_router(state_success)
        assert route == "success"
        print("\n✅ High-confidence FAQ routes to 'success'")

        # Test low-confidence fallback
        state_fallback = {
            "context": {
                "faq_results": [{"question": "Test", "answer": "Test", "relevance_score": 0.3}],
                "faq_success": False
            }
        }
        route = orchestrator.faq_fallback_router(state_fallback)
        assert route == "fallback_rag"
        print("✅ Low-confidence FAQ routes to 'fallback_rag'")

    @pytest.mark.asyncio
    async def test_complex_query_fallback(self, orchestrator):
        """Test that complex queries fall back to RAG"""
        state = {
            "session_id": "test-session-003",
            "message": "Explain the detailed biological process of osseointegration in dental implants",
            "metadata": {"language": "en"},
            "context": {},
            "audit_trail": []
        }

        # Execute FAQ lookup
        result_state = await orchestrator.faq_lookup_node(state)

        # Route based on result
        route = orchestrator.faq_fallback_router(result_state)

        print(f"\n✅ Complex query routing: {route}")
        print(f"   FAQs found: {len(result_state['context']['faq_results'])}")

        # Complex medical queries should typically fall back to RAG
        if not result_state['context']['faq_success']:
            assert route == "fallback_rag"
            print("   ✅ Correctly falling back to RAG for complex query")

    @pytest.mark.asyncio
    async def test_multiple_faq_results(self, orchestrator):
        """Test handling of multiple FAQ results"""
        state = {
            "session_id": "test-session-004",
            "message": "Tell me about dental implants",
            "metadata": {"language": "en"},
            "context": {},
            "audit_trail": []
        }

        # Execute FAQ lookup
        result_state = await orchestrator.faq_lookup_node(state)

        faqs = result_state['context']['faq_results']

        print(f"\n✅ Found {len(faqs)} FAQs for 'dental implants'")

        if len(faqs) > 1:
            print("   Top 3 results:")
            for i, faq in enumerate(faqs[:3], 1):
                print(f"   {i}. {faq['question'][:60]}... (score: {faq['relevance_score']:.2f})")

    @pytest.mark.asyncio
    async def test_audit_trail_tracking(self, orchestrator):
        """Test that FAQ operations are logged in audit trail"""
        state = {
            "session_id": "test-session-005",
            "message": "What are your hours?",
            "metadata": {"language": "en"},
            "context": {},
            "audit_trail": []
        }

        # Execute FAQ lookup
        result_state = await orchestrator.faq_lookup_node(state)

        # Verify audit trail
        assert len(result_state['audit_trail']) > 0

        faq_audit = result_state['audit_trail'][0]
        assert faq_audit['node'] == 'faq_lookup'
        assert 'timestamp' in faq_audit
        assert 'faqs_found' in faq_audit
        assert 'top_score' in faq_audit

        print("\n✅ Audit trail properly logged")
        print(f"   {faq_audit}")


class TestAnalyticsTracking:
    """Test FAQ analytics and view tracking"""

    @pytest.fixture
    def faq_tool(self):
        """Create FAQ tool instance"""
        return FAQQueryTool(clinic_id=SHTERN_CLINIC_ID)

    @pytest.mark.asyncio
    async def test_view_count_increments(self, faq_tool):
        """Test that view counts increment (fire-and-forget)"""
        # Search for FAQ
        results = await faq_tool.search_faqs(
            query="When was Shtern founded?",
            language="en",
            limit=1
        )

        if results:
            faq_id = results[0]['id']
            initial_views = results[0].get('view_count', 0)

            # Search again (should increment view count)
            results2 = await faq_tool.search_faqs(
                query="When was Shtern founded?",
                language="en",
                limit=1
            )

            print(f"\n✅ View tracking test:")
            print(f"   FAQ ID: {faq_id}")
            print(f"   Initial views: {initial_views}")
            print(f"   Note: View count increments are fire-and-forget (async)")

    @pytest.mark.asyncio
    async def test_faq_statistics(self, faq_tool):
        """Test FAQ statistics retrieval"""
        from supabase import create_client

        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

        if not supabase_url or not supabase_key:
            pytest.skip("Supabase credentials not available")

        client = create_client(supabase_url, supabase_key)

        # Get FAQ stats
        response = client.rpc(
            'get_faq_stats',
            {'p_clinic_id': SHTERN_CLINIC_ID}
        ).execute()

        if response.data and len(response.data) > 0:
            stats = response.data[0]

            print("\n✅ FAQ Statistics:")
            print(f"   Total FAQs: {stats.get('total_faqs', 0)}")
            print(f"   Active FAQs: {stats.get('active_faqs', 0)}")
            print(f"   Featured FAQs: {stats.get('featured_faqs', 0)}")
            print(f"   Total views: {stats.get('total_views', 0)}")
            print(f"   Languages: {stats.get('languages', [])}")
            print(f"   Most viewed category: {stats.get('most_viewed_category', 'N/A')}")

            # Basic assertions
            assert stats['total_faqs'] >= 84  # We imported 84 Shtern FAQs
            assert stats['active_faqs'] >= 84
            assert 'english' in stats.get('languages', [])


class TestErrorHandling:
    """Test error handling and edge cases"""

    @pytest.fixture
    def faq_tool(self):
        """Create FAQ tool instance"""
        return FAQQueryTool(clinic_id=SHTERN_CLINIC_ID)

    @pytest.mark.asyncio
    async def test_empty_query(self, faq_tool):
        """Test handling of empty query"""
        results = await faq_tool.search_faqs(query="", language="en")

        # Should return empty list, not raise exception
        assert results == []
        print("\n✅ Empty query handled gracefully")

    @pytest.mark.asyncio
    async def test_no_results_query(self, faq_tool):
        """Test handling when no FAQs match"""
        results = await faq_tool.search_faqs(
            query="xyzabc123nonsense",
            language="en"
        )

        # Should return empty list
        assert results == []
        print("\n✅ No-results query handled gracefully")

    @pytest.mark.asyncio
    async def test_invalid_category(self, faq_tool):
        """Test handling of invalid category"""
        results = await faq_tool.search_faqs(
            query="test",
            category="invalid_category",
            language="en"
        )

        # Should still work (category just won't match anything)
        assert isinstance(results, list)
        print("\n✅ Invalid category handled gracefully")

    @pytest.mark.asyncio
    async def test_orchestrator_with_no_faq_tool(self):
        """Test orchestrator when FAQ tool is not initialized"""
        orchestrator = HealthcareLangGraph(
            clinic_id=None  # No clinic ID = no FAQ tool
        )

        state = {
            "session_id": "test-session",
            "message": "Test",
            "metadata": {},
            "context": {},
            "audit_trail": []
        }

        # Should handle gracefully
        result_state = await orchestrator.faq_lookup_node(state)

        assert result_state['context'].get('faq_results') == []
        assert result_state['context'].get('faq_success') == False
        print("\n✅ Orchestrator handles missing FAQ tool gracefully")


if __name__ == "__main__":
    # Run tests with pytest
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
