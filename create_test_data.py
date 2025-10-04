#!/usr/bin/env python3
"""
Create test data for Rule Engine testing
"""

import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
import uuid

load_dotenv()

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be set")

from supabase.client import ClientOptions

# Configure client to use healthcare schema
options = ClientOptions(
    schema='healthcare',
    auto_refresh_token=True,
    persist_session=False
)

supabase: Client = create_client(supabase_url, supabase_key, options=options)

TEST_ORG_ID = "550e8400-e29b-41d4-a716-446655440100"
TEST_CLINIC_ID = "550e8400-e29b-41d4-a716-446655440000"
TEST_PATTERN_ID = "550e8400-e29b-41d4-a716-446655440010"


async def create_test_data():
    """Create test clinic and rules"""
    
    print("Creating test data for Rule Engine...")
    
    try:
        # 1. Create test clinic (skip organization check)
        print("\n1. Creating test clinic...")
        clinic_data = {
            "id": TEST_CLINIC_ID,
            "organization_id": TEST_ORG_ID,
            "name": "Test Clinic for Rule Engine",
            "address": "123 Test Street, Test City",
            "phone": "555-0100",
            "email": "test@ruleclinic.com",
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "tuesday": {"open": "09:00", "close": "17:00"},
                "wednesday": {"open": "09:00", "close": "17:00"},
                "thursday": {"open": "09:00", "close": "17:00"},
                "friday": {"open": "09:00", "close": "17:00"}
            },
            "is_active": True
        }
        
        # Try to create clinic (will fail if exists)
        try:
            result = supabase.table("clinics").insert(clinic_data).execute()
            print(f"  ✓ Created clinic: {TEST_CLINIC_ID}")
        except Exception as e:
            if "duplicate key" in str(e) or "already exists" in str(e):
                print(f"  ℹ Clinic already exists: {TEST_CLINIC_ID}")
            else:
                # Try without organization_id
                del clinic_data["organization_id"]
                try:
                    result = supabase.table("clinics").insert(clinic_data).execute()
                    print(f"  ✓ Created clinic without org: {TEST_CLINIC_ID}")
                except:
                    print(f"  ℹ Using existing clinic: {TEST_CLINIC_ID}")
        
        # 2. Create sample rules
        print("\n2. Creating sample rules...")
        
        rules = [
            {
                "clinic_id": TEST_CLINIC_ID,
                "rule_name": "Doctor Room Authorization",
                "rule_type": "hard_constraint",
                "scope": "clinic",
                "scope_id": TEST_CLINIC_ID,
                "precedence": 1000,
                "conditions": {
                    "type": "doctor_room",
                    "operator": "in",
                    "allowed_rooms": ["room_1", "room_2", "room_3"]
                },
                "actions": {
                    "reject": True,
                    "message": "Doctor not authorized for this room"
                },
                "rule_description": "Ensures doctors only use authorized rooms",
                "active": True
            },
            {
                "clinic_id": TEST_CLINIC_ID,
                "rule_name": "Business Hours Constraint",
                "rule_type": "hard_constraint",
                "scope": "clinic",
                "scope_id": TEST_CLINIC_ID,
                "precedence": 1100,
                "conditions": {
                    "type": "time_range",
                    "min_hour": 9,
                    "max_hour": 17
                },
                "actions": {
                    "reject": True,
                    "message": "Appointments must be within business hours (9 AM - 5 PM)"
                },
                "rule_description": "Enforces business hours",
                "active": True
            },
            {
                "clinic_id": TEST_CLINIC_ID,
                "rule_name": "Minimum Buffer Time",
                "rule_type": "hard_constraint",
                "scope": "clinic",
                "scope_id": TEST_CLINIC_ID,
                "precedence": 1200,
                "conditions": {
                    "type": "buffer_time",
                    "buffer_minutes": 15
                },
                "actions": {
                    "reject": True,
                    "message": "Insufficient buffer time between appointments"
                },
                "rule_description": "Requires 15 minute buffer between appointments",
                "active": True
            },
            {
                "clinic_id": TEST_CLINIC_ID,
                "rule_name": "Workload Balancing",
                "rule_type": "soft_preference",
                "scope": "clinic",
                "scope_id": TEST_CLINIC_ID,
                "precedence": 5000,
                "conditions": {
                    "type": "workload",
                    "metric": "appointments_today",
                    "max_appointments_per_day": 20
                },
                "actions": {
                    "score_modifier": -5,
                    "per_unit": "appointment",
                    "reason": "Prefer less busy doctors"
                },
                "rule_description": "Distributes appointments evenly among doctors",
                "active": True
            },
            {
                "clinic_id": TEST_CLINIC_ID,
                "rule_name": "Morning Preference",
                "rule_type": "soft_preference",
                "scope": "clinic",
                "scope_id": TEST_CLINIC_ID,
                "precedence": 5100,
                "conditions": {
                    "type": "time_preference",
                    "preferred_hours": [9, 10, 11]
                },
                "actions": {
                    "score_modifier": 10,
                    "reason": "Morning slots preferred"
                },
                "rule_description": "Gives preference to morning appointments",
                "active": True
            }
        ]
        
        # Delete existing rules for this clinic
        supabase.table("booking_rules").delete().eq("clinic_id", TEST_CLINIC_ID).execute()
        
        # Insert new rules
        for rule in rules:
            result = supabase.table("booking_rules").insert(rule).execute()
            print(f"  ✓ Created rule: {rule['rule_name']}")
        
        # 3. Create sample visit pattern
        print("\n3. Creating sample visit pattern...")
        
        pattern_data = {
            "id": TEST_PATTERN_ID,
            "clinic_id": TEST_CLINIC_ID,
            "name": "Two-Stage Treatment",
            "description": "Initial consultation followed by treatment",
            "visits": [
                {
                    "visit_number": 1,
                    "name": "Initial Consultation",
                    "service_id": "consultation",
                    "duration_minutes": 30,
                    "description": "Initial patient assessment"
                },
                {
                    "visit_number": 2,
                    "name": "Treatment Session",
                    "service_id": "treatment",
                    "duration_minutes": 60,
                    "offset_from_previous": {
                        "min_days": 3,
                        "max_days": 14,
                        "preferred_days": 7
                    },
                    "description": "Main treatment procedure"
                }
            ],
            "constraints": {
                "same_doctor": True,
                "same_location": True,
                "require_confirmation": True,
                "allow_partial_booking": False
            },
            "active": True
        }
        
        # Check if pattern exists
        existing = supabase.table("visit_patterns").select("id").eq("id", TEST_PATTERN_ID).execute()
        
        if not existing.data:
            result = supabase.table("visit_patterns").insert(pattern_data).execute()
            print(f"  ✓ Created pattern: Two-Stage Treatment")
        else:
            print(f"  ℹ Pattern already exists")
        
        # 4. Create test doctors
        print("\n4. Creating test doctors...")
        
        doctors = [
            {
                "id": "test_doctor_001",
                "clinic_id": TEST_CLINIC_ID,
                "name": "Dr. Smith",
                "email": "smith@testclinic.com",
                "phone": "555-0201",
                "specialization": "General",
                "is_active": True
            },
            {
                "id": "test_doctor_002",
                "clinic_id": TEST_CLINIC_ID,
                "name": "Dr. Johnson",
                "email": "johnson@testclinic.com",
                "phone": "555-0202",
                "specialization": "Specialist",
                "is_active": True
            }
        ]
        
        for doctor in doctors:
            existing = supabase.table("doctors").select("id").eq("id", doctor["id"]).execute()
            if not existing.data:
                result = supabase.table("doctors").insert(doctor).execute()
                print(f"  ✓ Created doctor: {doctor['name']}")
            else:
                print(f"  ℹ Doctor already exists: {doctor['name']}")
        
        # 5. Create test rooms
        print("\n5. Creating test rooms...")
        
        rooms = [
            {
                "id": "room_1",
                "clinic_id": TEST_CLINIC_ID,
                "name": "Exam Room 1",
                "room_type": "examination",
                "equipment": ["basic", "xray"],
                "is_active": True
            },
            {
                "id": "room_2",
                "clinic_id": TEST_CLINIC_ID,
                "name": "Exam Room 2",
                "room_type": "examination",
                "equipment": ["basic"],
                "is_active": True
            },
            {
                "id": "room_3",
                "clinic_id": TEST_CLINIC_ID,
                "name": "Treatment Room",
                "room_type": "treatment",
                "equipment": ["basic", "surgical"],
                "is_active": True
            }
        ]
        
        for room in rooms:
            existing = supabase.table("rooms").select("id").eq("id", room["id"]).execute()
            if not existing.data:
                result = supabase.table("rooms").insert(room).execute()
                print(f"  ✓ Created room: {room['name']}")
            else:
                print(f"  ℹ Room already exists: {room['name']}")
        
        print("\n✅ Test data creation complete!")
        print("\nTest IDs for reference:")
        print(f"  Clinic ID: {TEST_CLINIC_ID}")
        print(f"  Pattern ID: {TEST_PATTERN_ID}")
        print(f"  Doctor IDs: test_doctor_001, test_doctor_002")
        print(f"  Room IDs: room_1, room_2, room_3")
        
    except Exception as e:
        print(f"\n❌ Error creating test data: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(create_test_data())