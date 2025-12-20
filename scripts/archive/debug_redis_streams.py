"""
Debug script to inspect Redis Streams and identify stuck messages
"""
import os
import sys
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Add app to path
sys.path.insert(0, os.path.dirname(__file__))

from app.config import get_redis_client
from app.services.whatsapp_queue.config import CONSUMER_GROUP
from app.services.whatsapp_queue.queue import stream_key, dlq_key

def main():
    instance = "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"

    redis = get_redis_client()
    key = stream_key(instance)

    print(f"=== Redis Streams Debug for {instance} ===\n")

    # 1. Check stream info
    try:
        stream_info = redis.xinfo_stream(key)
        print(f"📊 Stream Info:")
        print(f"   Length: {stream_info.get('length', 0)}")
        print(f"   First entry: {stream_info.get('first-entry', 'N/A')}")
        print(f"   Last entry: {stream_info.get('last-entry', 'N/A')}")
        print()
    except Exception as e:
        print(f"❌ Error getting stream info: {e}\n")
        return

    # 2. Check consumer group info
    try:
        groups = redis.xinfo_groups(key)
        print(f"👥 Consumer Groups ({len(groups)}):")
        for group in groups:
            print(f"   - {group.get('name')}:")
            print(f"     Consumers: {group.get('consumers', 0)}")
            print(f"     Pending: {group.get('pending', 0)}")
            print(f"     Last delivered: {group.get('last-delivered-id', 'N/A')}")
        print()
    except Exception as e:
        print(f"❌ Error getting group info: {e}\n")

    # 3. Check consumers in group
    try:
        consumers = redis.xinfo_consumers(key, CONSUMER_GROUP)
        print(f"🔍 Consumers in '{CONSUMER_GROUP}' ({len(consumers)}):")
        for consumer in consumers:
            print(f"   - {consumer.get('name')}:")
            print(f"     Pending: {consumer.get('pending', 0)}")
            print(f"     Idle: {consumer.get('idle', 0)} ms")
        print()
    except Exception as e:
        print(f"❌ Error getting consumers: {e}\n")

    # 4. Check pending messages
    try:
        pending = redis.xpending(key, CONSUMER_GROUP)
        print(f"📋 Pending Messages Summary:")
        print(f"   Count: {pending.get('pending', 0)}")
        if pending.get('min'):
            print(f"   Min ID: {pending.get('min')}")
            print(f"   Max ID: {pending.get('max')}")
        print()

        # Get detailed pending info
        if pending.get('pending', 0) > 0:
            pending_details = redis.xpending_range(key, CONSUMER_GROUP, '-', '+', 10)
            print(f"   Detailed Pending Messages:")
            for msg in pending_details:
                print(f"     - ID: {msg['message_id']}")
                print(f"       Consumer: {msg['consumer']}")
                print(f"       Deliveries: {msg['time_since_delivered']} ms ago")
                print(f"       Delivery count: {msg['times_delivered']}")
    except Exception as e:
        print(f"❌ Error getting pending messages: {e}\n")

    # 5. Try to read all messages in stream (without consuming)
    try:
        all_messages = redis.xrange(key, '-', '+', count=10)
        print(f"\n📬 Messages in Stream ({len(all_messages)}):")
        for msg_id, fields in all_messages:
            print(f"   - ID: {msg_id}")
            print(f"     Fields: {fields}")
        print()
    except Exception as e:
        print(f"❌ Error reading stream: {e}\n")

    # 6. Suggest fixes
    print("=" * 60)
    print("🔧 SUGGESTED FIXES:")
    print()

    if pending.get('pending', 0) > 0:
        print("⚠️  There are PENDING messages claimed by idle/dead consumers.")
        print()
        print("Option 1: Claim messages for current worker")
        print(f"   redis-cli XCLAIM {key} {CONSUMER_GROUP} worker-new 0 <message-id>")
        print()
        print("Option 2: Reset consumer group (DANGER: loses pending state)")
        print(f"   redis-cli XGROUP SETID {key} {CONSUMER_GROUP} 0")
        print()
        print("Option 3: Delete and recreate consumer group")
        print(f"   redis-cli XGROUP DESTROY {key} {CONSUMER_GROUP}")
        print(f"   redis-cli XGROUP CREATE {key} {CONSUMER_GROUP} 0-0 MKSTREAM")
        print()
    elif stream_info.get('length', 0) > 0:
        print("⚠️  Messages exist but no pending messages.")
        print()
        print("This means messages have already been ACKed but not deleted,")
        print("OR they were added to the stream after the consumer group was created.")
        print()
        print("Check the last-delivered-id vs message IDs to diagnose.")

    print("=" * 60)

if __name__ == "__main__":
    main()