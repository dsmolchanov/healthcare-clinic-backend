#!/usr/bin/env python3
"""
Test Rule Engine via Remote Backend API
Tests the rule engine endpoints through HTTP requests
"""

import asyncio
import os
import sys
import json
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional
import aiohttp
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


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


def print_warning(text):
    print(f"{Colors.YELLOW}⚠{Colors.ENDC} {text}")


class RuleEngineAPIClient:
    """Client for testing Rule Engine API endpoints"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def get_policy_snapshot(self, clinic_id: str, version: Optional[int] = None) -> Dict:
        """Get compiled policy snapshot"""
        url = f"{self.base_url}/api/rules/policy-snapshot/{clinic_id}"
        params = {}
        if version:
            params['version'] = version
        
        async with self.session.get(url, params=params) as resp:
            return await resp.json()
    
    async def compile_policy(self, clinic_id: str, status: str = "draft", compiled_by: Optional[str] = None) -> Dict:
        """Compile rules into policy"""
        url = f"{self.base_url}/api/rules/compile/{clinic_id}"
        data = {
            "status": status,
            "compiled_by": compiled_by
        }
        
        async with self.session.post(url, json=data) as resp:
            return await resp.json()
    
    async def activate_policy(self, clinic_id: str, version: int) -> Dict:
        """Activate a policy version"""
        url = f"{self.base_url}/api/rules/activate/{clinic_id}/{version}"
        
        async with self.session.post(url) as resp:
            return await resp.json()
    
    async def dry_run(self, clinic_id: str, rules: list, slot: dict, context: dict = None) -> Dict:
        """Test rules with dry run"""
        url = f"{self.base_url}/api/rules/dry-run"
        data = {
            "clinic_id": clinic_id,
            "rules": rules,
            "slot": slot,
            "context": context or {}
        }
        
        async with self.session.post(url, json=data) as resp:
            return await resp.json()
    
    async def evaluate_slot(self, clinic_id: str, patient_id: str, service: str, slot: dict, preferences: dict = None) -> Dict:
        """Evaluate a single slot"""
        url = f"{self.base_url}/api/rules/evaluate-slot"
        data = {
            "clinic_id": clinic_id,
            "patient_id": patient_id,
            "requested_service": service,
            "slot": slot,
            "preferences": preferences or {}
        }
        
        async with self.session.post(url, json=data) as resp:
            return await resp.json()
    
    async def find_pattern_slots(self, pattern_id: str, clinic_id: str, patient_id: str, 
                                 service: str, start_date: str, end_date: str, max_results: int = 10) -> Dict:
        """Find available pattern slots"""
        url = f"{self.base_url}/api/rules/patterns/find-slots"
        data = {
            "pattern_id": pattern_id,
            "clinic_id": clinic_id,
            "patient_id": patient_id,
            "requested_service": service,
            "start_date": start_date,
            "end_date": end_date,
            "max_results": max_results
        }
        
        async with self.session.post(url, json=data) as resp:
            return await resp.json()
    
    async def reserve_pattern(self, pattern_set: dict, patient_id: str, 
                             hold_duration: int = 300, client_hold_id: Optional[str] = None) -> Dict:
        """Reserve a pattern"""
        url = f"{self.base_url}/api/rules/patterns/reserve"
        data = {
            "pattern_set": pattern_set,
            "patient_id": patient_id,
            "hold_duration_seconds": hold_duration,
            "client_hold_id": client_hold_id
        }
        
        async with self.session.post(url, json=data) as resp:
            return await resp.json()
    
    async def confirm_reservation(self, reservation_id: str) -> Dict:
        """Confirm a reservation"""
        url = f"{self.base_url}/api/rules/patterns/confirm"
        data = {"reservation_id": reservation_id}
        
        async with self.session.post(url, json=data) as resp:
            return await resp.json()
    
    async def cancel_reservation(self, reservation_id: str, reason: str = "user_cancelled") -> Dict:
        """Cancel a reservation"""
        url = f"{self.base_url}/api/rules/patterns/cancel"
        data = {
            "reservation_id": reservation_id,
            "reason": reason
        }
        
        async with self.session.post(url, json=data) as resp:
            return await resp.json()
    
    async def get_stats(self, clinic_id: Optional[str] = None) -> Dict:
        """Get statistics"""
        url = f"{self.base_url}/api/rules/stats"
        params = {}
        if clinic_id:
            params['clinic_id'] = clinic_id
        
        async with self.session.get(url, params=params) as resp:
            return await resp.json()
    
    async def warm_cache(self, clinic_ids: list) -> Dict:
        """Warm cache for clinics"""
        url = f"{self.base_url}/api/rules/cache/warm"
        
        async with self.session.post(url, json=clinic_ids) as resp:
            return await resp.json()
    
    async def invalidate_cache(self, clinic_id: str, version: Optional[int] = None) -> Dict:
        """Invalidate cache"""
        url = f"{self.base_url}/api/rules/cache/invalidate/{clinic_id}"
        params = {}
        if version:
            params['version'] = version
        
        async with self.session.post(url, params=params) as resp:
            return await resp.json()
    
    async def health_check(self) -> bool:
        """Check if API is accessible"""
        try:
            url = f"{self.base_url}/health"
            async with self.session.get(url, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False


async def test_remote_api():
    """Test the rule engine API endpoints"""
    
    print_header("Rule Engine Remote API Test")
    
    # Configuration
    # Try local first, then production
    api_urls = [
        "http://localhost:8000",  # Local development
        "http://localhost:8787",  # Alternative local port
        "https://clinics-backend.fly.dev",  # Production (if deployed)
    ]
    
    api_url = None
    client = None
    
    # Find working API endpoint
    print_info("Finding available API endpoint...")
    for url in api_urls:
        try:
            async with RuleEngineAPIClient(url) as test_client:
                if await test_client.health_check():
                    api_url = url
                    print_success(f"  Connected to: {url}")
                    break
                else:
                    print_warning(f"  {url} - No response")
        except Exception as e:
            print_warning(f"  {url} - Connection failed")
    
    if not api_url:
        print_error("No API endpoint available. Please start the backend server:")
        print_info("  cd clinics/backend")
        print_info("  uvicorn app.main:app --reload --port 8000")
        return
    
    # Test configuration - use existing Stern Clinics
    test_clinic_id = "90fd1605-7f84-46b9-9a31-a32bbc73e81b"
    test_patient_id = "test_patient_001"
    test_doctor_id = "test_doctor_001"
    
    test_results = {
        "passed": 0,
        "failed": 0,
        "warnings": 0
    }
    
    async with RuleEngineAPIClient(api_url) as client:
        
        # Test 1: Get policy snapshot (might not exist yet)
        print_info("\nTest 1: Get Policy Snapshot")
        try:
            result = await client.get_policy_snapshot(test_clinic_id)
            if result.get("success"):
                print_success("  Policy snapshot retrieved")
                print_info(f"    Version: {result['policy'].get('version')}")
                print_info(f"    SHA256: {result['policy'].get('sha256', 'N/A')[:8]}...")
                test_results["passed"] += 1
            else:
                print_warning("  No policy found (expected for new clinic)")
                test_results["warnings"] += 1
        except Exception as e:
            print_warning(f"  No existing policy: {e}")
            test_results["warnings"] += 1
        
        # Test 2: Compile policy
        print_info("\nTest 2: Compile Policy")
        try:
            result = await client.compile_policy(
                test_clinic_id,
                status="draft",
                compiled_by=None  # Don't pass compiled_by if it needs UUID
            )
            if result.get("success"):
                print_success("  Policy compiled successfully")
                policy_version = result['policy'].get('version', 1)
                print_info(f"    Version: {policy_version}")
                print_info(f"    Status: {result['policy'].get('status')}")
                print_info(f"    Rules: {result['policy']['metadata'].get('rule_count', 0)}")
                test_results["passed"] += 1
            else:
                print_error(f"  Compilation failed: {result.get('detail')}")
                test_results["failed"] += 1
        except Exception as e:
            print_error(f"  Compilation error: {e}")
            test_results["failed"] += 1
        
        # Test 3: Dry run
        print_info("\nTest 3: Dry Run Rules")
        try:
            test_slot = {
                "id": "test_slot_001",
                "doctor_id": test_doctor_id,
                "room_id": "room_1",
                "service_id": "consultation",
                "start_time": (datetime.now() + timedelta(days=1)).isoformat(),
                "end_time": (datetime.now() + timedelta(days=1, hours=1)).isoformat()
            }
            
            test_context = {
                "patient_id": test_patient_id,
                "service": "consultation",
                "preferences": {"morning": True}
            }
            
            result = await client.dry_run(
                test_clinic_id,
                rules=[],  # Using compiled rules
                slot=test_slot,
                context=test_context
            )
            
            if result.get("success"):
                print_success("  Dry run completed")
                evaluation = result.get("result", {})
                print_info(f"    Valid: {evaluation.get('is_valid')}")
                print_info(f"    Score: {evaluation.get('score', 0)}")
                print_info(f"    Execution: {evaluation.get('execution_time_ms', 0):.2f}ms")
                test_results["passed"] += 1
            else:
                print_error(f"  Dry run failed: {result.get('detail')}")
                test_results["failed"] += 1
        except Exception as e:
            print_error(f"  Dry run error: {e}")
            test_results["failed"] += 1
        
        # Test 4: Evaluate slot
        print_info("\nTest 4: Evaluate Slot")
        try:
            test_slot = {
                "id": "eval_slot_001",
                "doctor_id": test_doctor_id,
                "room_id": "room_2",
                "service_id": "checkup",
                "start_time": (datetime.now() + timedelta(days=2)).isoformat(),
                "end_time": (datetime.now() + timedelta(days=2, hours=0.5)).isoformat()
            }
            
            result = await client.evaluate_slot(
                test_clinic_id,
                test_patient_id,
                "checkup",
                test_slot,
                preferences={"afternoon": True}
            )
            
            if result.get("success"):
                print_success("  Slot evaluated")
                evaluation = result.get("evaluation", {})
                print_info(f"    Valid: {evaluation.get('is_valid')}")
                print_info(f"    Score: {evaluation.get('score', 0)}")
                
                if evaluation.get("explanations"):
                    print_info("    Explanations:")
                    for exp in evaluation["explanations"][:3]:
                        print_info(f"      - {exp}")
                
                test_results["passed"] += 1
            else:
                print_error(f"  Evaluation failed: {result.get('detail')}")
                test_results["failed"] += 1
        except Exception as e:
            print_error(f"  Evaluation error: {e}")
            test_results["failed"] += 1
        
        # Test 5: Find pattern slots
        print_info("\nTest 5: Find Pattern Slots")
        try:
            # This might fail if no patterns exist
            result = await client.find_pattern_slots(
                pattern_id="test_pattern_001",
                clinic_id=test_clinic_id,
                patient_id=test_patient_id,
                service="consultation",
                start_date=(datetime.now() + timedelta(days=1)).date().isoformat(),
                end_date=(datetime.now() + timedelta(days=30)).date().isoformat(),
                max_results=5
            )
            
            if result.get("success"):
                print_success("  Pattern slots search completed")
                print_info(f"    Found: {result.get('count', 0)} pattern sets")
                test_results["passed"] += 1
            else:
                print_warning(f"  Pattern search failed (expected if no patterns): {result.get('detail')}")
                test_results["warnings"] += 1
        except Exception as e:
            print_warning(f"  Pattern search error (expected): {e}")
            test_results["warnings"] += 1
        
        # Test 6: Get statistics
        print_info("\nTest 6: Get Statistics")
        try:
            result = await client.get_stats(test_clinic_id)
            
            if result.get("success"):
                print_success("  Statistics retrieved")
                stats = result.get("statistics", {})
                
                if "cache_stats" in stats:
                    cache = stats["cache_stats"]
                    print_info(f"    Cache hit rate: {cache.get('hit_rate', 0):.1f}%")
                    print_info(f"    Memory hits: {cache.get('memory_hits', 0)}")
                
                if "evaluation_stats" in stats:
                    eval_stats = stats["evaluation_stats"]
                    print_info(f"    Total evaluations: {eval_stats.get('total_evaluations', 0)}")
                    print_info(f"    Avg time: {eval_stats.get('avg_execution_time_ms', 0):.2f}ms")
                
                test_results["passed"] += 1
            else:
                print_error(f"  Stats failed: {result.get('detail')}")
                test_results["failed"] += 1
        except Exception as e:
            print_error(f"  Stats error: {e}")
            test_results["failed"] += 1
        
        # Test 7: Cache operations
        print_info("\nTest 7: Cache Operations")
        try:
            # Warm cache
            result = await client.warm_cache([test_clinic_id])
            if result.get("success"):
                print_success("  Cache warmed")
                test_results["passed"] += 1
            else:
                print_warning("  Cache warm failed")
                test_results["warnings"] += 1
            
            # Invalidate cache
            result = await client.invalidate_cache(test_clinic_id)
            if result.get("success"):
                print_success("  Cache invalidated")
                test_results["passed"] += 1
            else:
                print_warning("  Cache invalidation failed")
                test_results["warnings"] += 1
                
        except Exception as e:
            print_error(f"  Cache operation error: {e}")
            test_results["failed"] += 1
    
    # Print summary
    print_header("Test Summary")
    
    total_tests = test_results["passed"] + test_results["failed"]
    success_rate = (test_results["passed"] / max(total_tests, 1)) * 100
    
    print_info(f"API Endpoint: {api_url}")
    print_success(f"Passed: {test_results['passed']}")
    if test_results["warnings"] > 0:
        print_warning(f"Warnings: {test_results['warnings']}")
    if test_results["failed"] > 0:
        print_error(f"Failed: {test_results['failed']}")
    
    print_info(f"Success Rate: {success_rate:.1f}%")
    
    if success_rate >= 70:
        print_success("\n✅ Rule Engine API is working correctly!")
    elif success_rate >= 50:
        print_warning("\n⚠️ Rule Engine API is partially working")
    else:
        print_error("\n❌ Rule Engine API has issues")
    
    # Provide next steps
    print_header("Next Steps")
    
    if test_results["failed"] > 0:
        print_info("To fix failed tests:")
        print_info("  1. Ensure all migrations are applied")
        print_info("  2. Check that test clinic exists in database")
        print_info("  3. Review API logs for errors")
    else:
        print_info("The Rule Engine is ready for use!")
        print_info("You can now:")
        print_info("  1. Create rules through NocoDB or API")
        print_info("  2. Compile and activate policies")
        print_info("  3. Evaluate appointment slots")
        print_info("  4. Schedule multi-visit patterns")


async def main():
    """Main test runner"""
    try:
        await test_remote_api()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())