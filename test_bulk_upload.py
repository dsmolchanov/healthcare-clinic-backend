#!/usr/bin/env python3
"""Test the bulk upload endpoint locally"""

import asyncio
import os
from app.services.grok_multimodal_parser import GrokMultimodalParser, DiscoveryResult

async def test_parser():
    """Test the Grok parser directly"""

    # Check if API key is available
    api_key = os.getenv("XAI_API_KEY")
    print(f"XAI_API_KEY configured: {bool(api_key)}")
    print(f"XAI_API_KEY length: {len(api_key) if api_key else 0}")

    if not api_key:
        print("ERROR: XAI_API_KEY not found in environment")
        return

    try:
        # Initialize parser
        parser = GrokMultimodalParser(api_key=api_key)
        print(f"Parser initialized successfully")
        print(f"API URL: {parser.api_url}")
        print(f"Model: {parser.model}")

        # Test with simple CSV data
        test_csv = """first_name,last_name,specialization,phone,email
John,Smith,Cardiology,(555) 123-4567,john.smith@clinic.com
Jane,Doe,Pediatrics,(555) 987-6543,jane.doe@clinic.com
Robert,Johnson,Orthopedics,(555) 555-5555,robert.j@clinic.com"""

        print("\nTesting entity discovery with CSV data...")

        # Test discover_entities
        result = await parser.discover_entities(
            file_content=test_csv.encode('utf-8'),
            mime_type='text/csv',
            filename='test_doctors.csv'
        )

        print(f"\nDiscovery Result:")
        print(f"Entities found: {len(result.detected_entities)}")
        for entity in result.detected_entities:
            print(f"  - {entity.field_name}: {entity.suggested_table}.{entity.suggested_field} ({entity.confidence:.2f})")

    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()

    # Run test
    asyncio.run(test_parser())