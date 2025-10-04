"""
Test script for resilient multi-layer service search

This script tests all search layers:
1. Alias exact matching
2. Dual-language FTS (Russian + English)
3. FTS relaxed with prefix matching
4. Trigram fuzzy matching
5. Telemetry tracking
"""

import asyncio
import os
import sys
from typing import List, Dict
from supabase import create_client, Client

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.tools.price_query_tool import PriceQueryTool


# Test queries covering different scenarios
TEST_QUERIES = {
    "exact_alias_ru": [
        "пломба",
        "канал",
        "пломбу",
    ],
    "exact_alias_en": [
        "filling",
        "root canal",
        "rct",
    ],
    "fts_russian": [
        "композитная пломба",
        "лечение каналов",
        "удаление зуба",
    ],
    "fts_english": [
        "composite filling",
        "tooth extraction",
        "dental cleaning",
    ],
    "typo_tolerance": [
        "plomba",  # Mixed language typo
        "pломбa",  # Mixed Cyrillic/Latin
        "fillig",  # Missing 'n'
        "канал",  # Correct Russian
    ],
    "prefix_matching": [
        "пломб",  # Incomplete word
        "fill",   # Incomplete word
        "комп",   # Short prefix
    ],
    "edge_cases": [
        "root kanal",  # Mixed language
        "RCT treatment",  # Abbreviation
        "зубная пломба",  # Multi-word Russian
    ],
}


class SearchTester:
    """Test harness for resilient search"""

    def __init__(self, clinic_id: str):
        self.clinic_id = clinic_id

        # Initialize Supabase client
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

        if not supabase_url or not supabase_key:
            raise ValueError("Supabase credentials not configured")

        self.client: Client = create_client(supabase_url, supabase_key)
        self.tool = PriceQueryTool(clinic_id)

    async def test_search_query(self, query: str, category: str = "") -> Dict:
        """Test a single search query"""
        print(f"\n{'='*60}")
        print(f"Query: '{query}' | Category: '{category or 'any'}'")
        print(f"{'='*60}")

        results = await self.tool.get_services_by_query(
            query=query,
            category=category if category else None,
            limit=5
        )

        if not results:
            print("❌ NO RESULTS FOUND")
            return {
                "query": query,
                "category": category,
                "result_count": 0,
                "search_stage": "none"
            }

        print(f"✅ Found {len(results)} result(s)")
        print(f"🔍 Search Stage: {results[0].get('search_stage', 'unknown')}")
        print()

        for i, service in enumerate(results, 1):
            relevance = service.get('relevance_score', 0.0)
            print(f"{i}. {service['name']}")
            print(f"   Category: {service['category']}")
            print(f"   Price: {service['price']} {service['currency']}")
            print(f"   Relevance: {relevance:.4f}")
            if service.get('description'):
                desc = service['description'][:80] + "..." if len(service['description']) > 80 else service['description']
                print(f"   Description: {desc}")
            print()

        return {
            "query": query,
            "category": category,
            "result_count": len(results),
            "search_stage": results[0].get('search_stage', 'unknown'),
            "top_result": results[0]['name'],
            "top_relevance": results[0].get('relevance_score', 0.0)
        }

    async def test_all_categories(self):
        """Test all predefined query categories"""
        all_results = {}

        for category_name, queries in TEST_QUERIES.items():
            print(f"\n{'#'*60}")
            print(f"# Testing Category: {category_name.upper()}")
            print(f"{'#'*60}")

            category_results = []
            for query in queries:
                result = await self.test_search_query(query)
                category_results.append(result)
                await asyncio.sleep(0.1)  # Small delay between queries

            all_results[category_name] = category_results

        return all_results

    async def print_summary(self, all_results: Dict):
        """Print summary statistics"""
        print(f"\n{'='*60}")
        print("SEARCH SUMMARY")
        print(f"{'='*60}\n")

        total_queries = 0
        total_found = 0
        stage_counts = {}

        for category_name, results in all_results.items():
            print(f"{category_name.upper()}:")
            found = sum(1 for r in results if r['result_count'] > 0)
            total = len(results)
            total_queries += total
            total_found += found

            print(f"  Found: {found}/{total} ({found/total*100:.1f}%)")

            # Count search stages
            for result in results:
                stage = result['search_stage']
                stage_counts[stage] = stage_counts.get(stage, 0) + 1

            print()

        print(f"Overall Success Rate: {total_found}/{total_queries} ({total_found/total_queries*100:.1f}%)")
        print(f"\nSearch Stage Distribution:")
        for stage, count in sorted(stage_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {stage}: {count} queries ({count/total_queries*100:.1f}%)")

    async def check_telemetry(self):
        """Check search telemetry data"""
        print(f"\n{'='*60}")
        print("SEARCH TELEMETRY (Last 20 searches)")
        print(f"{'='*60}\n")

        try:
            response = self.client.schema('healthcare').table('search_telemetry').select(
                'search_query, search_stage, result_count, latency_ms, created_at'
            ).eq('clinic_id', self.clinic_id).order('created_at', desc=True).limit(20).execute()

            if response.data:
                for entry in response.data:
                    print(f"Query: '{entry['search_query'][:40]}...' | "
                          f"Stage: {entry['search_stage']} | "
                          f"Results: {entry['result_count']} | "
                          f"Latency: {entry.get('latency_ms', 0)}ms")
            else:
                print("No telemetry data available")

        except Exception as e:
            print(f"Could not fetch telemetry: {e}")

    async def seed_test_aliases(self):
        """Seed test aliases using the helper function"""
        print(f"\n{'='*60}")
        print("SEEDING TEST ALIASES")
        print(f"{'='*60}\n")

        try:
            response = self.client.rpc(
                'seed_common_service_aliases',
                {'p_clinic_id': self.clinic_id}
            ).execute()

            count = response.data if response.data else 0
            print(f"✅ Seeded {count} aliases")

        except Exception as e:
            print(f"⚠️  Could not seed aliases: {e}")
            print("This is expected if the function doesn't exist yet or aliases already exist")


async def main():
    """Main test runner"""
    # Get clinic ID from environment or use default
    clinic_id = os.environ.get('TEST_CLINIC_ID')

    if not clinic_id:
        print("❌ ERROR: TEST_CLINIC_ID environment variable not set")
        print("\nUsage:")
        print("  export TEST_CLINIC_ID='your-clinic-uuid'")
        print("  python tests/test_resilient_search.py")
        sys.exit(1)

    print(f"Testing resilient search for clinic: {clinic_id}")

    tester = SearchTester(clinic_id)

    # Seed test aliases
    await tester.seed_test_aliases()

    # Run all tests
    all_results = await tester.test_all_categories()

    # Print summary
    await tester.print_summary(all_results)

    # Check telemetry
    await tester.check_telemetry()

    print(f"\n{'='*60}")
    print("✅ Testing complete!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
