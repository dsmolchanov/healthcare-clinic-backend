#!/usr/bin/env python3
"""Test enhanced field mapping suggestions"""

import asyncio
from app.services.grok_multimodal_parser import GrokMultimodalParser

async def test_enhanced_mapping():
    """Test that field mapping suggestions work with high confidence"""

    # Test CSV with common field names that should auto-map
    test_csv = b"""first_name,last_name,email,phone,languages,specialization,years_of_experience,education
John,Doe,john.doe@clinic.com,(555) 123-4567,"English, Spanish",Cardiology,15,Harvard Medical School
Jane,Smith,jane.smith@clinic.com,(555) 987-6543,"English, French",Pediatrics,8,Stanford University"""

    print("=== TESTING ENHANCED FIELD MAPPING ===\n")

    try:
        # Initialize Grok parser
        parser = GrokMultimodalParser()
        print("✅ Grok parser initialized\n")

        # Test entity discovery
        print("Testing entity discovery with common field names...")
        result = await parser.discover_entities(
            test_csv,
            "text/csv",
            "test_enhanced_mapping.csv"
        )

        print(f"✅ Discovery successful!\n")
        print(f"Entities discovered: {len(result.detected_entities)}\n")
        print("Field Mapping Suggestions:")
        print("-" * 60)

        # Check confidence scores
        high_confidence_count = 0
        auto_mapped_count = 0

        for entity in result.detected_entities:
            # Show field mapping with confidence
            confidence_indicator = ""
            if entity.confidence >= 0.95:
                confidence_indicator = " ✅ AUTO"
                auto_mapped_count += 1
            elif entity.confidence >= 0.80:
                confidence_indicator = " ✓ HIGH"
                high_confidence_count += 1
            elif entity.confidence >= 0.70:
                confidence_indicator = " - MEDIUM"
            else:
                confidence_indicator = " ? LOW"

            print(f"{entity.field_name:25} → {entity.suggested_table}.{entity.suggested_field:20} [{entity.confidence:.2f}]{confidence_indicator}")

        print("-" * 60)
        print(f"\nSummary:")
        print(f"  Auto-mapped (≥95% confidence): {auto_mapped_count} fields")
        print(f"  High confidence (≥80%): {high_confidence_count} fields")
        print(f"  Total fields: {len(result.detected_entities)}")

        # Test if important fields are auto-mapped
        expected_auto_maps = {
            "first_name": "first_name",
            "last_name": "last_name",
            "email": "email",
            "phone": "phone",
            "specialization": "specialization"
        }

        print(f"\nValidating expected mappings:")
        for field_name, expected_target in expected_auto_maps.items():
            entity = next((e for e in result.detected_entities if e.field_name.lower() == field_name), None)
            if entity:
                if entity.suggested_field == expected_target and entity.confidence >= 0.80:
                    print(f"  ✅ {field_name} → {expected_target} (confidence: {entity.confidence:.2f})")
                else:
                    print(f"  ❌ {field_name} → {entity.suggested_field} (expected {expected_target}, confidence: {entity.confidence:.2f})")
            else:
                print(f"  ❌ {field_name} not found in results")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_enhanced_mapping())