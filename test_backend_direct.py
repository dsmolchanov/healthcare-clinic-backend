#!/usr/bin/env python3
"""
Direct backend test with curl to bypass Python timeout issues
"""

import subprocess
import time
import json

BACKEND_URL = "https://healthcare-clinic-backend.fly.dev"

def test_with_curl():
    """Test using curl with timeout"""

    payload = {
        "from_phone": "widget_curl_test",
        "to_phone": "+14155238886",
        "body": "Hello",
        "message_sid": f"test_{int(time.time())}",
        "clinic_id": "3e411ecb-3411-4add-91e2-8fa897310cb0",
        "clinic_name": "Test Clinic",
        "channel": "widget",
        "metadata": {
            "session_id": "curl_test",
            "agent_id": "test-agent"
        }
    }

    print("="*60)
    print("CURL TEST")
    print("="*60)
    print(f"URL: {BACKEND_URL}/api/process-message")
    print(f"Payload: {json.dumps(payload, indent=2)}\n")

    # Try different timeouts
    for timeout in [10, 30, 60]:
        print(f"\n⏱️  Trying with {timeout}s timeout...")
        print("-"*60)

        start = time.time()

        cmd = [
            'curl',
            '-X', 'POST',
            f'{BACKEND_URL}/api/process-message',
            '-H', 'Content-Type: application/json',
            '-d', json.dumps(payload),
            '--max-time', str(timeout),
            '-v',  # Verbose
            '-w', '\\n\\nHTTP_CODE:%{http_code}\\nTIME_TOTAL:%{time_total}\\n'
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 5
            )

            elapsed = time.time() - start

            print(f"\n📊 Results after {elapsed:.2f}s:")
            print(f"   Return code: {result.returncode}")

            if result.returncode == 0:
                print(f"\n✅ SUCCESS!")
                print(f"\nResponse:")
                print(result.stdout)

                # Try to parse JSON from response
                try:
                    # Extract JSON from output
                    lines = result.stdout.split('\n')
                    for i, line in enumerate(lines):
                        if line.strip().startswith('{'):
                            json_str = '\n'.join(lines[i:])
                            # Remove curl stats
                            if 'HTTP_CODE' in json_str:
                                json_str = json_str.split('HTTP_CODE')[0]
                            data = json.loads(json_str)
                            print(f"\n📥 Parsed response:")
                            print(json.dumps(data, indent=2))
                            return True
                except Exception as e:
                    print(f"⚠️  Could not parse JSON: {e}")

                return True

            elif result.returncode == 28:  # Curl timeout
                print(f"   ⏱️  Timeout (curl code 28)")
                if result.stderr:
                    print(f"\n   Stderr:\n{result.stderr}")

            else:
                print(f"   ❌  Error")
                if result.stdout:
                    print(f"\n   Stdout:\n{result.stdout}")
                if result.stderr:
                    print(f"\n   Stderr:\n{result.stderr}")

        except subprocess.TimeoutExpired:
            print(f"   ⏱️  Process timeout")
        except Exception as e:
            print(f"   ❌  Exception: {e}")

    print("\n❌ All attempts failed")
    return False


if __name__ == "__main__":
    print("\n" + "🔵" * 60)
    print("BACKEND DIRECT TEST (CURL)")
    print("🔵" * 60 + "\n")

    success = test_with_curl()

    if success:
        print("\n🎉 Test PASSED")
        exit(0)
    else:
        print("\n❌ Test FAILED - Backend is not responding")
        print("\nPossible issues:")
        print("  1. Backend code is hanging on initialization")
        print("  2. Missing or invalid environment variables")
        print("  3. Database connection issues")
        print("  4. External service (OpenAI/Pinecone) timeout")
        print("\nRecommended actions:")
        print("  1. Check backend logs: fly logs --app healthcare-clinic-backend")
        print("  2. Verify secrets: fly ssh console --app healthcare-clinic-backend")
        print("  3. Restart backend: fly apps restart healthcare-clinic-backend")
        exit(1)