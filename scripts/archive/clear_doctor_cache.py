"""Clear bad Redis cache for doctor info"""
import asyncio
from app.config import get_redis_client

def clear_cache():
    redis_client = get_redis_client()
    if redis_client:
        key = "clinic_doctors:e0c84f56-235d-49f2-9a44-37c1be579afc"
        result = redis_client.delete(key)
        print(f"✅ Deleted cache key '{key}': {result} key(s) removed")
        
        # Verify it's gone
        check = redis_client.get(key)
        if check is None:
            print(f"✅ Confirmed: Cache key is now empty")
        else:
            print(f"⚠️ Warning: Cache key still exists: {check}")
    else:
        print("❌ No Redis client available")

if __name__ == "__main__":
    clear_cache()
