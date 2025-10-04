"""
Fix consumer group to read all messages from beginning
This resets the last-delivered-id to '0' so the worker can consume existing messages
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from app.config import get_redis_client
from app.services.whatsapp_queue.config import CONSUMER_GROUP
from app.services.whatsapp_queue.queue import stream_key

def main():
    instance = "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"
    redis = get_redis_client()
    key = stream_key(instance)

    print(f"🔧 Fixing consumer group for {instance}")
    print(f"   Stream: {key}")
    print(f"   Group: {CONSUMER_GROUP}\n")

    # Check current state
    try:
        groups = redis.xinfo_groups(key)
        print("Current state:")
        for g in groups:
            if g.get("name") == CONSUMER_GROUP:
                print(f"   Consumers: {g.get('consumers', 0)}")
                print(f"   Pending: {g.get('pending', 0)}")
                print(f"   Last delivered: {g.get('last-delivered-id', 'N/A')}\n")
    except Exception as e:
        print(f"Error checking groups: {e}\n")

    # Reset last-delivered-id to beginning
    print("Resetting last-delivered-id to '0' (read all messages)...")
    try:
        redis.xgroup_setid(key, CONSUMER_GROUP, id='0')
        print("✅ Consumer group reset successfully!\n")
    except Exception as e:
        print(f"❌ Error: {e}\n")
        return

    # Verify
    try:
        groups = redis.xinfo_groups(key)
        print("New state:")
        for g in groups:
            if g.get("name") == CONSUMER_GROUP:
                print(f"   Consumers: {g.get('consumers', 0)}")
                print(f"   Pending: {g.get('pending', 0)}")
                print(f"   Last delivered: {g.get('last-delivered-id', 'N/A')}")
    except Exception as e:
        print(f"Error verifying: {e}")

    print("\n✅ Done! Worker should now be able to consume messages.")

if __name__ == "__main__":
    main()