#!/usr/bin/env python3
"""
View Langfuse traces for the dental conversation
"""
import os
import sys
from dotenv import load_dotenv
from langfuse import Langfuse
from datetime import datetime, timedelta

# Load environment
load_dotenv()

# Initialize Langfuse client
langfuse = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")
)

def format_trace(trace):
    """Format trace for display"""
    return f"""
{'='*80}
Trace ID: {trace.id}
Name: {trace.name}
Session ID: {trace.session_id or 'N/A'}
User ID: {trace.user_id or 'N/A'}
Timestamp: {trace.timestamp}
Duration: {trace.duration if hasattr(trace, 'duration') else 'N/A'}ms
Metadata: {trace.metadata if hasattr(trace, 'metadata') else 'N/A'}
{'='*80}
"""

def main():
    print("\n🔍 Fetching Langfuse traces...")
    print(f"📊 Host: {os.getenv('LANGFUSE_HOST')}")
    print(f"🔑 Public Key: {os.getenv('LANGFUSE_PUBLIC_KEY')[:20]}...")

    try:
        # Fetch traces from the last hour
        traces = langfuse.get_traces(
            limit=20,
            from_timestamp=datetime.now() - timedelta(hours=1)
        )

        print(f"\n✅ Found {len(traces.data) if hasattr(traces, 'data') else 0} traces")

        if hasattr(traces, 'data') and traces.data:
            for idx, trace in enumerate(traces.data[:10], 1):  # Show first 10
                print(f"\n📝 Trace #{idx}")
                print(format_trace(trace))
        else:
            print("\n⚠️  No traces found in the last hour")
            print("\nPossible reasons:")
            print("  1. No LLM calls were made with Langfuse tracing enabled")
            print("  2. Traces are being sent but not yet processed")
            print("  3. Different session IDs are being used")

        # Try to get specific session
        print("\n\n🔎 Searching for WhatsApp session traces...")
        session_traces = langfuse.get_traces(
            limit=10,
            from_timestamp=datetime.now() - timedelta(days=1)
        )

        print(f"Total traces in last 24h: {len(session_traces.data) if hasattr(session_traces, 'data') else 0}")

        # Access dashboard
        print("\n\n🌐 Langfuse Dashboard:")
        print(f"   URL: {os.getenv('LANGFUSE_HOST')}")
        print(f"   Login with your account credentials")
        print(f"\n   Direct link to traces:")
        print(f"   {os.getenv('LANGFUSE_HOST')}/traces")

    except Exception as e:
        print(f"\n❌ Error fetching traces: {e}")
        print(f"\nDebug info:")
        print(f"  - Exception type: {type(e).__name__}")
        print(f"  - Exception message: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
