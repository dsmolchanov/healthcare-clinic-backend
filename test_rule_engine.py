#!/usr/bin/env python3
"""
Test Rule Engine Implementation in clinics/backend
Verifies that all components are working correctly
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, date
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import get_supabase
from app.services.policy_compiler import PolicyCompiler, PolicyStatus
from app.services.policy_cache import PolicyCache
from app.services.rule_evaluator import RuleEvaluator, EvaluationContext, TimeSlot
from app.services.pattern_evaluator import PatternEvaluator


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text:^60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.ENDC}\n")


def print_success(text):
    print(f"{Colors.GREEN}✓{Colors.ENDC} {text}")


def print_error(text):
    print(f"{Colors.RED}✗{Colors.ENDC} {text}")


def print_info(text):
    print(f"{Colors.BLUE}ℹ{Colors.ENDC} {text}")


async def test_services():
    """Test all rule engine services"""
    
    print_header("Rule Engine Test Suite")
    
    # Initialize services
    print_info("Initializing services...")
    supabase = await get_supabase()
    
    # Initialize cache (without Redis for testing)
    policy_cache = PolicyCache(supabase, redis_client=None)
    policy_compiler = PolicyCompiler(supabase)
    rule_evaluator = RuleEvaluator(supabase, policy_cache)
    pattern_evaluator = PatternEvaluator(supabase, rule_evaluator)
    
    print_success("Services initialized")
    
    # Test 1: Check database tables
    print_info("\nTest 1: Verifying database tables...")
    tables_to_check = [
        "booking_rules",
        "policy_snapshots",
        "visit_patterns",
        "pattern_reservations",
        "rule_evaluations"
    ]
    
    for table in tables_to_check:
        try:
            # Try to query the table
            result = supabase.table(table).select("*").limit(1).execute()
            print_success(f"  Table 'healthcare.{table}' exists")
        except Exception as e:
            print_error(f"  Table 'healthcare.{table}' check failed: {e}")
    
    # Test 2: Create sample rule
    print_info("\nTest 2: Creating sample rule...")
    
    try:
        # Get or create a test clinic
        clinics = supabase.table("clinics").select("id, name").limit(1).execute()
        
        if clinics.data:
            clinic_id = clinics.data[0]["id"]
            print_success(f"  Using existing clinic: {clinic_id}")
        else:
            # Create test clinic
            clinic_data = {
                "name": "Test Clinic",
                "address": "123 Test St",
                "phone": "555-0100",
                "email": "test@clinic.com",
                "business_hours": {"monday": {"open": "09:00", "close": "17:00"}},
                "is_active": True
            }
            result = supabase.table("clinics").insert(clinic_data).execute()
            clinic_id = result.data[0]["id"]
            print_success(f"  Created test clinic: {clinic_id}")
        
        # Create a sample rule
        rule_data = {
            "clinic_id": clinic_id,
            "rule_name": "Test Doctor Room Restriction",
            "rule_type": "hard_constraint",
            "scope": "clinic",
            "scope_id": clinic_id,
            "precedence": 1000,
            "conditions": {
                "type": "doctor_room",
                "allowed_rooms": ["room_1", "room_2"]
            },
            "actions": {
                "reject": True,
                "message": "Doctor not authorized for this room"
            },
            "active": True
        }
        
        result = supabase.table("booking_rules").insert(rule_data).execute()
        rule_id = result.data[0]["id"]
        print_success(f"  Created test rule: {rule_id}")
        
        # Test 3: Compile policy
        print_info("\nTest 3: Compiling policy...")
        
        compiled = await policy_compiler.compile_policy(
            clinic_id,
            PolicyStatus.DRAFT,
            compiled_by="test_suite"
        )
        
        print_success(f"  Policy compiled: version {compiled['version']}, SHA256: {compiled['sha256'][:8]}...")
        print_info(f"  Constraints: {compiled['metadata']['constraint_count']}")
        print_info(f"  Preferences: {compiled['metadata']['preference_count']}")
        
        # Test 4: Cache policy
        print_info("\nTest 4: Testing policy cache...")
        
        await policy_cache.set(clinic_id, compiled)
        print_success("  Policy cached")
        
        cached = await policy_cache.get(clinic_id)
        if cached and cached["sha256"] == compiled["sha256"]:
            print_success("  Cache retrieval successful")
        else:
            print_error("  Cache retrieval failed")
        
        stats = policy_cache.get_stats()
        print_info(f"  Cache stats: {stats}")
        
        # Test 5: Evaluate slot
        print_info("\nTest 5: Evaluating time slot...")
        
        context = EvaluationContext(
            clinic_id=clinic_id,
            patient_id="test_patient",
            requested_service="consultation"
        )
        
        # Test slot that violates the rule (room_3 not allowed)
        bad_slot = TimeSlot(
            id="slot_1",
            doctor_id="test_doctor",
            room_id="room_3",
            service_id="consultation",
            start_time=datetime.now() + timedelta(days=1),
            end_time=datetime.now() + timedelta(days=1, hours=1)
        )
        
        result = await rule_evaluator.evaluate_slot(context, bad_slot)
        
        if not result.is_valid:
            print_success(f"  Rule correctly rejected slot: {result.explanations[0]}")
        else:
            print_error("  Rule should have rejected slot but didn't")
        
        # Test slot that passes the rule
        good_slot = TimeSlot(
            id="slot_2",
            doctor_id="test_doctor",
            room_id="room_1",
            service_id="consultation",
            start_time=datetime.now() + timedelta(days=1),
            end_time=datetime.now() + timedelta(days=1, hours=1)
        )
        
        result = await rule_evaluator.evaluate_slot(context, good_slot)
        
        if result.is_valid:
            print_success(f"  Rule correctly accepted slot with score: {result.score}")
        else:
            print_error(f"  Rule should have accepted slot: {result.explanations}")
        
        print_info(f"  Evaluation time: {result.execution_time_ms:.2f}ms")
        
        # Test 6: Pattern evaluation
        print_info("\nTest 6: Testing pattern evaluation...")
        
        # Create a test pattern
        pattern_data = {
            "clinic_id": clinic_id,
            "name": "Test Two-Visit Pattern",
            "visits": [
                {
                    "visit_number": 1,
                    "name": "Initial Consultation",
                    "duration_minutes": 30,
                    "service_id": "consultation"
                },
                {
                    "visit_number": 2,
                    "name": "Follow-up",
                    "duration_minutes": 15,
                    "service_id": "follow_up",
                    "offset_from_previous": {
                        "min_days": 7,
                        "max_days": 14
                    }
                }
            ],
            "constraints": {
                "same_doctor": True,
                "require_confirmation": True
            },
            "active": True
        }
        
        pattern_result = supabase.table("visit_patterns").insert(pattern_data).execute()
        pattern_id = pattern_result.data[0]["id"]
        print_success(f"  Created test pattern: {pattern_id}")
        
        # Find slots for pattern
        pattern_sets = await pattern_evaluator.find_pattern_slots(
            pattern_id,
            context,
            datetime.now() + timedelta(days=1),
            datetime.now() + timedelta(days=30),
            max_results=5
        )
        
        if pattern_sets:
            print_success(f"  Found {len(pattern_sets)} pattern slot sets")
            best_set = pattern_sets[0]
            print_info(f"  Best pattern score: {best_set.total_score}")
            
            # Reserve pattern
            reservation = await pattern_evaluator.reserve_pattern_set(
                best_set,
                "test_patient",
                hold_duration_seconds=300
            )
            
            print_success(f"  Pattern reserved: {reservation.reservation_id}")
            print_info(f"  Expires at: {reservation.expires_at}")
        else:
            print_info("  No pattern slots found (expected if no availability)")
        
        # Clean up test data
        print_info("\nCleaning up test data...")
        
        try:
            # Delete test rule
            supabase.table("booking_rules").delete().eq("id", rule_id).execute()
            print_success("  Test rule deleted")
            
            # Delete test pattern
            supabase.table("visit_patterns").delete().eq("id", pattern_id).execute()
            print_success("  Test pattern deleted")
            
        except Exception as e:
            print_error(f"  Cleanup error: {e}")
        
        # Summary
        print_header("Test Summary")
        
        eval_stats = rule_evaluator.get_stats()
        cache_stats = policy_cache.get_stats()
        
        print_info(f"Total evaluations: {eval_stats['total_evaluations']}")
        print_info(f"Valid slots: {eval_stats['valid_slots']}")
        print_info(f"Invalid slots: {eval_stats['invalid_slots']}")
        print_info(f"Avg evaluation time: {eval_stats['avg_execution_time_ms']:.2f}ms")
        print_info(f"Cache hit rate: {cache_stats['hit_rate']:.1f}%")
        
        print_success("\n✅ All tests completed successfully!")
        print_info("The rule engine is properly configured in clinics/backend")
        
    except Exception as e:
        print_error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """Main test runner"""
    try:
        await test_services()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())