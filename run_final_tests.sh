#!/bin/bash

echo "======================================================================"
echo "DENTAL CLINIC ONBOARDING SYSTEM - FINAL TEST EXECUTION"
echo "======================================================================"
echo "Date: $(date)"
echo "======================================================================"
echo ""

# Count total tests
echo "Collecting tests..."
total_tests=$(python3 -m pytest tests/ --co -q 2>/dev/null | wc -l)
echo "Total tests found: $total_tests"
echo ""

# Run tests by category
echo "Running test suites..."
echo "----------------------------------------------------------------------"

# Security tests
echo -n "Security Tests: "
python3 -m pytest tests/test_security.py -q --tb=no --disable-warnings 2>&1 | tail -1

# Privacy tests
echo -n "Privacy Compliance Tests: "
python3 -m pytest tests/test_privacy_compliance.py -q --tb=no --disable-warnings 2>&1 | tail -1

# Appointment tests
echo -n "Appointment Tests: "
python3 -m pytest tests/test_appointments.py -q --tb=no --disable-warnings 2>&1 | tail -1

# WhatsApp tests
echo -n "WhatsApp Integration Tests: "
python3 -m pytest tests/test_whatsapp.py -q --tb=no --disable-warnings 2>&1 | tail -1

# Onboarding tests
echo -n "Onboarding Tests: "
python3 -m pytest tests/test_onboarding.py -q --tb=no --disable-warnings 2>&1 | tail -1

# End-to-end tests
echo -n "End-to-End Tests: "
python3 -m pytest tests/test_end_to_end.py -q --tb=no --disable-warnings 2>&1 | tail -1

# Performance tests
echo -n "Performance Tests: "
python3 -m pytest tests/test_performance.py -q --tb=no --disable-warnings 2>&1 | tail -1

echo ""
echo "======================================================================"
echo "SUMMARY"
echo "======================================================================"

# Run all tests and capture summary
python3 -m pytest tests/ --tb=no --disable-warnings -q 2>&1 | tail -10

echo ""
echo "======================================================================"
echo "Test execution completed at: $(date)"
echo "======================================================================"
