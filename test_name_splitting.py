#!/usr/bin/env python3
"""Test Grok parser's ability to handle combined name fields"""

import asyncio
from app.services.grok_multimodal_parser import GrokMultimodalParser

async def test_name_splitting():
    """Test that Grok parser correctly splits combined name fields"""

    # Test CSV with combined name field
    test_csv = b"""name,specialization,email,phone
Dr. Robert Anderson,Orthodontics,robert.anderson@clinic.com,(555) 111-2222
Sarah Mitchell,Pediatrics,sarah.mitchell@clinic.com,(555) 333-4444
James Wilson Jr.,Cardiology,james.wilson@clinic.com,(555) 555-6666
Maria Garcia Lopez,Internal Medicine,maria.garcia@clinic.com,(555) 777-8888"""

    print("=== TESTING NAME FIELD SPLITTING ===\n")

    try:
        # Initialize Grok parser
        parser = GrokMultimodalParser()
        print("✅ Grok parser initialized\n")

        # Test entity discovery
        print("Testing entity discovery with combined 'name' field...")
        result = await parser.discover_entities(
            test_csv,
            "text/csv",
            "test_combined_names.csv"
        )

        print(f"✅ Discovery successful!\n")
        print(f"Entities discovered: {len(result.detected_entities)}\n")

        # Check if name was properly split
        found_first_name = False
        found_last_name = False
        found_combined_name = False

        for entity in result.detected_entities:
            print(f"Field: {entity.field_name}")
            print(f"  - Suggested: {entity.suggested_table}.{entity.suggested_field}")
            print(f"  - Samples: {entity.sample_values}")
            print(f"  - Confidence: {entity.confidence}")
            if entity.metadata:
                print(f"  - Metadata: {entity.metadata}")
            print()

            if entity.suggested_field == "first_name":
                found_first_name = True
                print("  ✅ Found first_name field!")
            elif entity.suggested_field == "last_name":
                found_last_name = True
                print("  ✅ Found last_name field!")
            elif "name" in entity.field_name.lower() and "first" not in entity.field_name.lower():
                found_combined_name = True
                print("  ⚠️ Found combined name field - should have been split!")

        print("\n=== RESULTS ===")
        if found_first_name and found_last_name and not found_combined_name:
            print("✅ SUCCESS: Name field was properly split into first_name and last_name")
        elif found_combined_name:
            print("❌ FAILED: Combined name field was not split")
        else:
            print(f"⚠️ PARTIAL: first_name={found_first_name}, last_name={found_last_name}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_name_splitting())