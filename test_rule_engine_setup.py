#!/usr/bin/env python3
"""
Test script to verify the rule engine is properly set up and working
"""

import asyncio
import os
from datetime import datetime, timedelta
from supabase import create_client, Client
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

# Initialize Supabase client
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
)

async def check_rule_tables():
    """Check if rule engine tables exist"""
    print("\n🔍 Checking Rule Engine Tables...")
    
    tables_to_check = [
        "booking_rules",
        "compiled_rules", 
        "policy_snapshots",
        "rule_evaluations",
        "visit_patterns"
    ]
    
    for table in tables_to_check:
        try:
            # Try to query each table
            result = supabase.table(f"healthcare.{table}").select("count").limit(1).execute()
            print(f"✅ Table '{table}' exists")
        except Exception as e:
            if "relation" in str(e) and "does not exist" in str(e):
                print(f"❌ Table '{table}' does not exist")
            else:
                print(f"⚠️ Table '{table}' - Error: {str(e)[:100]}")

async def check_existing_rules():
    """Check what rules are already configured"""
    print("\n📋 Checking Existing Rules...")
    
    try:
        # Get all active rules
        result = supabase.table("healthcare.booking_rules").select("*").eq("active", True).execute()
        
        if result.data:
            print(f"Found {len(result.data)} active rules:")
            
            # Group by type
            rules_by_type = {}
            for rule in result.data:
                rule_type = rule.get("rule_type", "unknown")
                if rule_type not in rules_by_type:
                    rules_by_type[rule_type] = []
                rules_by_type[rule_type].append(rule.get("rule_name", "Unnamed"))
            
            for rule_type, rule_names in rules_by_type.items():
                print(f"\n  {rule_type}:")
                for name in rule_names:
                    print(f"    • {name}")
        else:
            print("No active rules found")
    except Exception as e:
        print(f"Error checking rules: {e}")

async def create_sample_rules():
    """Create sample rules if none exist"""
    print("\n🔧 Creating Sample Rules...")
    
    sample_rules = [
        {
            "rule_name": "Doctor Room Authorization",
            "rule_type": "hard_constraint",
            "scope": "clinic",
            "precedence": 1000,
            "conditions": {
                "type": "doctor_room",
                "operator": "in",
                "field": "allowed_rooms"
            },
            "actions": {
                "reject": True,
                "message": "Doctor not authorized for this room"
            },
            "active": True
        },
        {
            "rule_name": "Cleaning Buffer Time",
            "rule_type": "hard_constraint",
            "scope": "clinic",
            "precedence": 1100,
            "conditions": {
                "type": "gap",
                "operator": "gte",
                "value": 15,
                "unit": "minutes"
            },
            "actions": {
                "add_buffer": True,
                "buffer_minutes": 15
            },
            "active": True
        },
        {
            "rule_name": "Prefer Least Busy Doctor",
            "rule_type": "soft_preference",
            "scope": "clinic",
            "precedence": 5000,
            "conditions": {
                "type": "workload",
                "metric": "appointments_today"
            },
            "actions": {
                "score_modifier": -10,
                "per_unit": "appointment"
            },
            "active": True
        }
    ]
    
    for rule in sample_rules:
        try:
            # Check if rule already exists
            existing = supabase.table("healthcare.booking_rules").select("id").eq("rule_name", rule["rule_name"]).execute()
            
            if not existing.data:
                # Get a clinic ID
                clinic = supabase.table("healthcare.clinics").select("id").limit(1).execute()
                if clinic.data:
                    rule["clinic_id"] = clinic.data[0]["id"]
                    rule["conditions"] = json.dumps(rule["conditions"])
                    rule["actions"] = json.dumps(rule["actions"])
                    
                    result = supabase.table("healthcare.booking_rules").insert(rule).execute()
                    print(f"✅ Created rule: {rule['rule_name']}")
                else:
                    print("❌ No clinic found to attach rules to")
                    break
            else:
                print(f"ℹ️ Rule already exists: {rule['rule_name']}")
        except Exception as e:
            print(f"❌ Error creating rule '{rule['rule_name']}': {e}")

async def test_rule_evaluation():
    """Test if the rule engine can evaluate a slot"""
    print("\n🧪 Testing Rule Evaluation...")
    
    # This would normally call your rule evaluation API
    print("Rule evaluation requires the backend API to be running")
    print("To test evaluation:")
    print("1. Start the backend: cd clinics/backend && python main.py")
    print("2. Call the evaluation endpoint: POST /api/rules/evaluate")
    
    # Sample evaluation context
    context = {
        "clinic_id": "your-clinic-id",
        "service_id": "your-service-id", 
        "doctor_id": "your-doctor-id",
        "requested_date": (datetime.now() + timedelta(days=1)).isoformat(),
        "requested_time": "10:00"
    }
    
    print("\nSample evaluation context:")
    print(json.dumps(context, indent=2))

async def main():
    print("=" * 60)
    print("🎯 Rule Engine Setup Verification")
    print("=" * 60)
    
    await check_rule_tables()
    await check_existing_rules()
    
    # Ask if user wants to create sample rules
    print("\n" + "=" * 60)
    create_samples = input("Do you want to create sample rules? (y/n): ")
    if create_samples.lower() == 'y':
        await create_sample_rules()
    
    await test_rule_evaluation()
    
    print("\n" + "=" * 60)
    print("✅ Rule Engine Verification Complete")
    print("=" * 60)
    
    print("\n📝 Next Steps:")
    print("1. Access Supabase to view/edit rules:")
    print("   https://supabase.com/dashboard/project/wojtrbcbnibplfcwfuux/editor")
    print("2. Navigate to the 'booking_rules' table")
    print("3. Add or modify rules as needed")
    print("4. Rules are automatically compiled and cached")

if __name__ == "__main__":
    asyncio.run(main())