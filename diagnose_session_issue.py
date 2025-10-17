"""
Diagnostic script to understand session/organization mismatch
"""

# The data you provided earlier shows:
correct_session = '4db648b8-ec3f-430d-89c0-adf3221e663b'
correct_org = 'e0c84f56-235d-49f2-9a44-37c1be579afc'

# The URL you're viewing shows:
viewed_session = '503da1c5-89ea-45f1-b507-7f5513ba0fac'
viewed_org = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'

print("=" * 60)
print("DIAGNOSIS: Organization/Session Mismatch")
print("=" * 60)
print()
print("YOUR DATA (from conversation_logs):")
print(f"  Session ID: {correct_session}")
print(f"  Organization: {correct_org}")
print(f"  Phone: 79857608984")
print(f"  Messages: 3+")
print()
print("WHAT YOU'RE VIEWING:")
print(f"  Session ID: {viewed_session}")
print(f"  Organization: {viewed_org}")
print(f"  Messages: 0")
print()
print("=" * 60)
print("ROOT CAUSE")
print("=" * 60)
print()
print("You are logged in as organization:")
print(f"  {viewed_org}")
print()
print("But your WhatsApp messages were stored in organization:")
print(f"  {correct_org}")
print()
print("The frontend filters conversations by YOUR logged-in organization,")
print("so it can't see the messages in the OTHER organization.")
print()
print("=" * 60)
print("SOLUTION")
print("=" * 60)
print()
print("Option 1: Log in with the correct organization")
print(f"  - Switch to organization: {correct_org}")
print()
print("Option 2: Check which organization should be used")
print("  - Are you managing multiple clinics?")
print("  - Should all conversations be in one organization?")
print()
print("Option 3: Migrate messages to correct organization")
print("  - Move all messages from org {correct_org}")
print(f"    to org {viewed_org}")
print()
print("=" * 60)
print("NEXT STEPS")
print("=" * 60)
print()
print("1. Check your user profile - which organization are you in?")
print("2. Check localStorage in browser - what is organizationId set to?")
print("3. Verify which organization the WhatsApp integration is configured for")
print()
