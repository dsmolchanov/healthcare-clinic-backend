#!/usr/bin/env python3
"""
Create sample rules for Stern Clinics
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from supabase.client import ClientOptions

load_dotenv()

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be set")

# Configure client to use healthcare schema
options = ClientOptions(
    schema='healthcare',
    auto_refresh_token=True,
    persist_session=False
)

supabase: Client = create_client(supabase_url, supabase_key, options=options)

STERN_CLINIC_ID = "90fd1605-7f84-46b9-9a31-a32bbc73e81b"


def create_stern_rules():
    """Create rules for Stern Clinics"""
    
    print("Creating rules for Stern Clinics...")
    
    try:
        # Delete existing rules for Stern Clinics
        print("\nRemoving existing rules...")
        supabase.table("booking_rules").delete().eq("clinic_id", STERN_CLINIC_ID).execute()
        
        # Create sample rules
        rules = [
            {
                "clinic_id": STERN_CLINIC_ID,
                "rule_name": "Business Hours",
                "rule_type": "hard_constraint",
                "scope": "clinic",
                "scope_id": STERN_CLINIC_ID,
                "precedence": 100,
                "conditions": {
                    "type": "time_range",
                    "min_hour": 8,
                    "max_hour": 18
                },
                "actions": {
                    "reject": True,
                    "message": "Appointments must be within business hours (8 AM - 6 PM)"
                },
                "rule_description": "Enforces clinic business hours",
                "active": True
            },
            {
                "clinic_id": STERN_CLINIC_ID,
                "rule_name": "Lunch Break",
                "rule_type": "hard_constraint",
                "scope": "clinic",
                "scope_id": STERN_CLINIC_ID,
                "precedence": 200,
                "conditions": {
                    "type": "time_range",
                    "blocked_hours": [12, 13],
                    "reason": "lunch_break"
                },
                "actions": {
                    "reject": True,
                    "message": "No appointments during lunch break (12 PM - 1 PM)"
                },
                "rule_description": "Blocks appointments during lunch",
                "active": True
            },
            {
                "clinic_id": STERN_CLINIC_ID,
                "rule_name": "Cleaning Buffer",
                "rule_type": "hard_constraint",
                "scope": "clinic",
                "scope_id": STERN_CLINIC_ID,
                "precedence": 300,
                "conditions": {
                    "type": "buffer_time",
                    "buffer_minutes": 10,
                    "reason": "cleaning"
                },
                "actions": {
                    "reject": True,
                    "message": "10 minute buffer required between appointments for cleaning"
                },
                "rule_description": "Ensures cleaning time between patients",
                "active": True
            },
            {
                "clinic_id": STERN_CLINIC_ID,
                "rule_name": "Morning Preference",
                "rule_type": "soft_preference",
                "scope": "clinic",
                "scope_id": STERN_CLINIC_ID,
                "precedence": 5000,
                "conditions": {
                    "type": "time_preference",
                    "preferred_hours": [8, 9, 10, 11]
                },
                "actions": {
                    "score_modifier": 15,
                    "reason": "Morning slots preferred for better patient experience"
                },
                "rule_description": "Gives preference to morning appointments",
                "active": True
            },
            {
                "clinic_id": STERN_CLINIC_ID,
                "rule_name": "Doctor Workload Balance",
                "rule_type": "soft_preference",
                "scope": "clinic",
                "scope_id": STERN_CLINIC_ID,
                "precedence": 5100,
                "conditions": {
                    "type": "workload",
                    "metric": "daily_appointments",
                    "max_appointments_per_day": 15
                },
                "actions": {
                    "score_modifier": -10,
                    "per_unit": "appointment_over_8",
                    "reason": "Distribute workload evenly"
                },
                "rule_description": "Balances appointments across doctors",
                "active": True
            },
            {
                "clinic_id": STERN_CLINIC_ID,
                "rule_name": "Emergency Time Slots",
                "rule_type": "soft_preference",
                "scope": "clinic",
                "scope_id": STERN_CLINIC_ID,
                "precedence": 5200,
                "conditions": {
                    "type": "emergency_buffer",
                    "reserved_slots_per_day": 2,
                    "hours": [11, 16]
                },
                "actions": {
                    "score_modifier": -20,
                    "reason": "Keep slots available for emergencies"
                },
                "rule_description": "Reserves slots for emergency patients",
                "active": True
            }
        ]
        
        print("\nCreating rules...")
        for rule in rules:
            result = supabase.table("booking_rules").insert(rule).execute()
            if result.data:
                print(f"  ✓ Created: {rule['rule_name']}")
                print(f"    Type: {rule['rule_type']}")
                print(f"    Precedence: {rule['precedence']}")
            else:
                print(f"  ✗ Failed: {rule['rule_name']}")
        
        # Create a visit pattern
        print("\nCreating visit pattern...")
        pattern_data = {
            "clinic_id": STERN_CLINIC_ID,
            "name": "Dental Treatment Series",
            "description": "Initial exam followed by treatment and follow-up",
            "visits": [
                {
                    "visit_number": 1,
                    "name": "Initial Examination",
                    "service_id": "dental_exam",
                    "duration_minutes": 45,
                    "description": "Comprehensive dental examination and X-rays"
                },
                {
                    "visit_number": 2,
                    "name": "Treatment",
                    "service_id": "dental_treatment",
                    "duration_minutes": 90,
                    "offset_from_previous": {
                        "min_days": 3,
                        "max_days": 10,
                        "preferred_days": 7
                    },
                    "description": "Primary treatment procedure"
                },
                {
                    "visit_number": 3,
                    "name": "Follow-up",
                    "service_id": "dental_followup",
                    "duration_minutes": 30,
                    "offset_from_previous": {
                        "min_days": 7,
                        "max_days": 21,
                        "preferred_days": 14
                    },
                    "description": "Post-treatment check-up"
                }
            ],
            "constraints": {
                "same_doctor": True,
                "same_location": True,
                "require_confirmation": True,
                "allow_partial_booking": False,
                "max_total_days": 30
            },
            "active": True
        }
        
        result = supabase.table("visit_patterns").insert(pattern_data).execute()
        if result.data:
            print(f"  ✓ Created pattern: Dental Treatment Series")
            print(f"    Pattern ID: {result.data[0]['id']}")
        
        print("\n✅ Rules created successfully for Stern Clinics!")
        print(f"\nClinic ID: {STERN_CLINIC_ID}")
        print(f"Total rules: {len(rules)}")
        print(f"  - Hard constraints: {len([r for r in rules if r['rule_type'] == 'hard_constraint'])}")
        print(f"  - Soft preferences: {len([r for r in rules if r['rule_type'] == 'soft_preference'])}")
        
    except Exception as e:
        print(f"\n❌ Error creating rules: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    create_stern_rules()