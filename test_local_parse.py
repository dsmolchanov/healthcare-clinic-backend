#!/usr/bin/env python3
"""Test parsing locally to see what's happening"""

import asyncio
import json
from app.services.grok_multimodal_parser import GrokMultimodalParser, FieldMapping

async def test_parsing():
    """Test the parse_and_import method locally"""

    test_csv = b"""first_name,last_name,specialization,phone,email
Alice,Johnson,Orthodontics,(555) 111-2222,alice.johnson@shtern.com
Bob,Williams,Endodontics,(555) 333-4444,bob.williams@shtern.com"""

    # Create mappings
    mappings = [
        FieldMapping("first_name", "doctors", "first_name", "string", None),
        FieldMapping("last_name", "doctors", "last_name", "string", None),
        FieldMapping("specialization", "doctors", "specialization", "string", None),
        FieldMapping("phone", "doctors", "phone", "phone", None),
        FieldMapping("email", "doctors", "email", "email", None),
    ]

    try:
        parser = GrokMultimodalParser()
        print("✅ Parser initialized\n")

        # Test parse and import
        result = await parser.parse_and_import(
            test_csv,
            "text/csv",
            mappings,
            "test-clinic-id"
        )

        print("Parse and Import Result:")
        print(f"Success: {result.success}")
        print(f"Imported counts: {result.imported}")
        print(f"Details: {json.dumps(result.details, indent=2)[:1000]}")

        # Check if doctors data is present
        if "data" in result.details and "doctors" in result.details["data"]:
            doctors = result.details["data"]["doctors"]
            print(f"\nDoctors found: {len(doctors)}")
            if doctors:
                print(f"First doctor: {json.dumps(doctors[0], indent=2)}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_parsing())