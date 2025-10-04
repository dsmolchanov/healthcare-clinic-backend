#!/usr/bin/env python3
"""
Main test runner for dental clinic system
Runs all test suites with coverage reporting
"""

import sys
import unittest
import coverage
import argparse
from datetime import datetime


def run_tests(test_type='all', verbose=False, with_coverage=True):
    """
    Run test suites

    Args:
        test_type: Type of tests to run ('all', 'security', 'privacy', 'appointments',
                   'whatsapp', 'integration', 'performance')
        verbose: Enable verbose output
        with_coverage: Generate coverage report
    """

    # Initialize coverage if requested
    cov = None
    if with_coverage:
        cov = coverage.Coverage()
        cov.start()

    # Create test loader
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Load test modules based on type
    test_modules = {
        'security': ['test_security'],
        'privacy': ['test_privacy_compliance'],
        'appointments': ['test_appointments'],
        'whatsapp': ['test_whatsapp'],
        'integration': ['test_end_to_end'],
        'performance': ['test_performance'],
        'all': [
            'test_security',
            'test_privacy_compliance',
            'test_appointments',
            'test_whatsapp',
            'test_end_to_end',
            'test_performance'
        ]
    }

    modules_to_run = test_modules.get(test_type, test_modules['all'])

    print(f"\n{'='*60}")
    print(f"Dental Clinic System - Test Suite")
    print(f"Running: {test_type.upper()} tests")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # Load tests
    for module_name in modules_to_run:
        try:
            module = __import__(module_name)
            suite.addTests(loader.loadTestsFromModule(module))
            print(f"✓ Loaded {module_name}")
        except ImportError as e:
            print(f"✗ Failed to load {module_name}: {e}")

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)

    # Generate coverage report
    if with_coverage and cov:
        cov.stop()

        print("\n" + "="*60)
        print("COVERAGE REPORT")
        print("="*60)

        # Print coverage report
        cov.report(omit=[
            '*/tests/*',
            '*/test_*.py',
            '*/venv/*',
            '*/virtualenv/*'
        ])

        # Generate HTML coverage report
        cov.html_report(directory='coverage_html_report')
        print(f"\nHTML coverage report generated in: coverage_html_report/index.html")

    # Print summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")

    if result.wasSuccessful():
        print("\n✅ ALL TESTS PASSED!")
    else:
        print("\n❌ SOME TESTS FAILED")

        if result.failures:
            print("\nFailed tests:")
            for test, trace in result.failures:
                print(f"  - {test}")

        if result.errors:
            print("\nTests with errors:")
            for test, trace in result.errors:
                print(f"  - {test}")

    print("="*60 + "\n")

    # Return exit code
    return 0 if result.wasSuccessful() else 1


def run_quick_check():
    """Run quick smoke tests for CI/CD"""
    print("\n🚀 Running quick smoke tests...")

    # Import critical tests
    from test_security import TestWebhookSecurity
    from test_privacy_compliance import TestPrivacyNotice
    from test_appointments import TestAppointmentBooking

    suite = unittest.TestSuite()

    # Add specific critical tests
    suite.addTest(TestWebhookSecurity('test_twilio_signature_verification_enabled'))
    suite.addTest(TestPrivacyNotice('test_privacy_notice_sent_on_first_contact'))
    suite.addTest(TestAppointmentBooking('test_successful_appointment_booking'))

    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Run tests for dental clinic system'
    )

    parser.add_argument(
        'type',
        nargs='?',
        default='all',
        choices=['all', 'security', 'privacy', 'appointments',
                 'whatsapp', 'integration', 'performance', 'quick'],
        help='Type of tests to run (default: all)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    parser.add_argument(
        '--no-coverage',
        action='store_true',
        help='Disable coverage reporting'
    )

    parser.add_argument(
        '--quick',
        action='store_true',
        help='Run quick smoke tests only'
    )

    args = parser.parse_args()

    if args.quick or args.type == 'quick':
        exit_code = run_quick_check()
    else:
        exit_code = run_tests(
            test_type=args.type,
            verbose=args.verbose,
            with_coverage=not args.no_coverage
        )

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
