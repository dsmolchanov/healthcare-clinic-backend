#!/usr/bin/env python3
"""Test if Grok API is working"""

import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

async def test_grok_api():
    """Test Grok API connectivity and functionality"""

    xai_api_key = os.getenv("XAI_API_KEY")

    print("=== TESTING GROK API ===\n")
    print(f"API Key present: {'Yes' if xai_api_key else 'No'}")

    if xai_api_key:
        print(f"API Key prefix: {xai_api_key[:10]}...")
        print(f"API Key length: {len(xai_api_key)}")
    else:
        print("ERROR: XAI_API_KEY not found in environment")
        return

    try:
        # Try to import and initialize Grok parser
        from app.services.grok_multimodal_parser import GrokMultimodalParser

        print("\nInitializing Grok parser...")
        parser = GrokMultimodalParser(api_key=xai_api_key)
        print("✅ Grok parser initialized successfully")

        # Test with simple CSV data
        test_csv = b"""name,role,email
Alice Smith,Manager,alice@company.com
Bob Jones,Developer,bob@company.com"""

        print("\nTesting entity discovery...")
        result = await parser.discover_entities(
            test_csv,
            "text/csv",
            "test.csv"
        )

        print(f"✅ Discovery successful!")
        print(f"   - Entities found: {len(result.detected_entities)}")
        print(f"   - Summary: {result.summary}")

        for entity in result.detected_entities:
            print(f"   - Field: {entity.field_name} -> {entity.suggested_table}.{entity.suggested_field}")

    except Exception as e:
        print(f"\n❌ Error testing Grok API: {e}")
        print(f"   Error type: {type(e).__name__}")

        # Try with OpenAI as fallback
        print("\nTrying OpenAI parser as fallback...")
        try:
            from app.services.openai_multimodal_parser import OpenAIMultimodalParser
            parser = OpenAIMultimodalParser()
            print("✅ OpenAI parser works as fallback")
        except Exception as e2:
            print(f"❌ OpenAI also failed: {e2}")

if __name__ == "__main__":
    asyncio.run(test_grok_api())