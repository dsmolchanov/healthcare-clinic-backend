"""
Test script for the price query tool
"""

import asyncio
import os
from app.tools.price_query_tool import query_service_prices, PriceQueryTool

# Set up environment
os.environ.setdefault("SUPABASE_URL", "https://wojtrbcbezpfwksedjmy.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", os.environ.get("SUPABASE_ANON_KEY", ""))


async def test_price_queries():
    """Test various price queries"""

    clinic_id = "3e411ecb-3411-4add-91e2-8fa897310cb0"  # Your test clinic

    print("=" * 80)
    print("Testing Price Query Tool")
    print("=" * 80)

    # Test 1: Query all services
    print("\n1. Querying all services:")
    result = await query_service_prices(clinic_id=clinic_id, limit=5)
    print(result)

    # Test 2: Query specific service (Russian)
    print("\n2. Querying 'пломба' (filling):")
    result = await query_service_prices(clinic_id=clinic_id, query="пломба", limit=3)
    print(result)

    # Test 3: Query by category
    print("\n3. Querying by category:")
    tool = PriceQueryTool(clinic_id)
    categories = await tool.get_all_categories()
    print(f"Available categories: {categories}")

    if categories:
        result = await query_service_prices(clinic_id=clinic_id, category=categories[0], limit=3)
        print(f"\nServices in '{categories[0]}':")
        print(result)

    # Test 4: Search for common services
    print("\n4. Searching for common services:")
    for term in ["чистка", "отбеливание", "консультация"]:
        result = await query_service_prices(clinic_id=clinic_id, query=term, limit=2)
        print(f"\nSearch '{term}':")
        print(result)

if __name__ == "__main__":
    asyncio.run(test_price_queries())
