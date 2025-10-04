#!/usr/bin/env python3
"""
Comprehensive test runner for the dental clinic onboarding system.
Executes all test suites and provides a summary report.
"""

import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List, Tuple

class TestRunner:
    def __init__(self):
        self.test_suites = [
            ("Security Tests", "tests/test_security.py"),
            ("Privacy Compliance Tests", "tests/test_privacy_compliance.py"),
            ("Appointment Tests", "tests/test_appointments.py"),
            ("WhatsApp Integration Tests", "tests/test_whatsapp.py"),
            ("Onboarding Tests", "tests/test_onboarding.py"),
            ("End-to-End Tests", "tests/test_end_to_end.py"),
            ("Performance Tests", "tests/test_performance.py"),
        ]
        self.results: Dict[str, Dict] = {}

    def run_test_suite(self, name: str, test_file: str) -> Tuple[bool, str, float]:
        """Run a single test suite and capture results."""
        print(f"\n{'='*60}")
        print(f"Running: {name}")
        print(f"{'='*60}")

        start_time = time.time()

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", test_file, "-v", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=60
            )

            elapsed_time = time.time() - start_time

            # Parse pytest output for pass/fail counts
            output = result.stdout + result.stderr
            passed = failed = 0

            for line in output.split('\n'):
                if 'passed' in line and 'warning' not in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if 'passed' in part and i > 0:
                            try:
                                passed = int(parts[i-1])
                            except (ValueError, IndexError):
                                pass

                if 'failed' in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if 'failed' in part and i > 0:
                            try:
                                failed = int(parts[i-1])
                            except (ValueError, IndexError):
                                pass

            success = result.returncode == 0

            # Store results
            self.results[name] = {
                'success': success,
                'passed': passed,
                'failed': failed,
                'elapsed_time': elapsed_time,
                'output': output
            }

            if success:
                print(f"✅ {name}: PASSED ({passed} tests in {elapsed_time:.2f}s)")
            else:
                print(f"❌ {name}: FAILED ({failed} failures out of {passed+failed} tests)")
                if failed > 0:
                    # Show failed test details
                    for line in output.split('\n'):
                        if 'FAILED' in line or 'ERROR' in line:
                            print(f"   - {line.strip()}")

            return success, output, elapsed_time

        except subprocess.TimeoutExpired:
            elapsed_time = 60.0
            self.results[name] = {
                'success': False,
                'passed': 0,
                'failed': 0,
                'elapsed_time': elapsed_time,
                'output': 'Test suite timed out after 60 seconds'
            }
            print(f"⏱️  {name}: TIMEOUT (exceeded 60s)")
            return False, "Timeout", elapsed_time

        except Exception as e:
            elapsed_time = time.time() - start_time
            self.results[name] = {
                'success': False,
                'passed': 0,
                'failed': 0,
                'elapsed_time': elapsed_time,
                'output': str(e)
            }
            print(f"⚠️  {name}: ERROR - {e}")
            return False, str(e), elapsed_time

    def run_all_tests(self):
        """Run all test suites."""
        print("\n" + "="*60)
        print("DENTAL CLINIC ONBOARDING SYSTEM - COMPREHENSIVE TEST SUITE")
        print("="*60)
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        total_start = time.time()

        for name, test_file in self.test_suites:
            self.run_test_suite(name, test_file)

        total_elapsed = time.time() - total_start

        # Generate summary report
        self.generate_report(total_elapsed)

    def generate_report(self, total_elapsed: float):
        """Generate a comprehensive test report."""
        print("\n" + "="*60)
        print("TEST EXECUTION SUMMARY")
        print("="*60)

        total_passed = sum(r['passed'] for r in self.results.values())
        total_failed = sum(r['failed'] for r in self.results.values())
        successful_suites = sum(1 for r in self.results.values() if r['success'])
        total_suites = len(self.results)

        print(f"\nTest Suites: {successful_suites}/{total_suites} passed")
        print(f"Total Tests: {total_passed} passed, {total_failed} failed")
        print(f"Total Time: {total_elapsed:.2f} seconds")

        print("\n" + "-"*60)
        print("DETAILED RESULTS BY SUITE:")
        print("-"*60)

        for name, result in self.results.items():
            status = "✅ PASS" if result['success'] else "❌ FAIL"
            print(f"\n{name}:")
            print(f"  Status: {status}")
            print(f"  Tests: {result['passed']} passed, {result['failed']} failed")
            print(f"  Time: {result['elapsed_time']:.2f}s")

        # Feature readiness assessment
        print("\n" + "="*60)
        print("FEATURE READINESS ASSESSMENT")
        print("="*60)

        features = {
            "Security & Authentication": self.results.get("Security Tests", {}).get('success', False),
            "Privacy Compliance (LFPDPPP)": self.results.get("Privacy Compliance Tests", {}).get('success', False),
            "Appointment Booking": self.results.get("Appointment Tests", {}).get('success', False),
            "WhatsApp Integration": self.results.get("WhatsApp Integration Tests", {}).get('success', False),
            "Clinic Onboarding": self.results.get("Onboarding Tests", {}).get('success', False),
            "End-to-End Flows": self.results.get("End-to-End Tests", {}).get('success', False),
            "Performance Standards": self.results.get("Performance Tests", {}).get('success', False),
        }

        for feature, ready in features.items():
            status = "✅ Ready" if ready else "❌ Not Ready"
            print(f"  {feature}: {status}")

        # Overall readiness
        readiness_pct = (sum(1 for r in features.values() if r) / len(features)) * 100

        print("\n" + "="*60)
        print(f"OVERALL SYSTEM READINESS: {readiness_pct:.0f}%")
        print("="*60)

        if readiness_pct >= 100:
            print("🎉 System is FULLY READY for production deployment!")
            print("✅ All features tested and validated")
            print("✅ Mexican market requirements satisfied")
            print("✅ HIPAA-ready architecture in place")
        elif readiness_pct >= 80:
            print("✅ System is ready for BETA deployment")
            print("⚠️  Some non-critical features need attention")
        else:
            print("❌ System needs more work before deployment")
            print("⚠️  Critical features are not ready")

        # Recommendations
        if total_failed > 0:
            print("\n" + "-"*60)
            print("RECOMMENDATIONS:")
            print("-"*60)
            for name, result in self.results.items():
                if not result['success'] and result['failed'] > 0:
                    print(f"  • Fix {result['failed']} failing tests in {name}")

        print("\n" + "="*60)
        print(f"Test run completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*60)

def main():
    """Main entry point."""
    runner = TestRunner()
    runner.run_all_tests()

    # Exit with appropriate code
    if all(r['success'] for r in runner.results.values()):
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
