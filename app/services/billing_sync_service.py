"""
Billing Sync Service
Handles synchronization of doctor counts to Stripe subscriptions.
Extracted to avoid HTTP loopback and enable reuse across triggers.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

import stripe

from app.database import get_core_client, get_healthcare_client, get_main_client

logger = logging.getLogger(__name__)

# Per-doctor price ID - loaded from DB on first use
_per_doctor_price_id: Optional[str] = None


async def get_per_doctor_price_id() -> Optional[str]:
    """Get the Stripe price ID for per-doctor tier (cached)."""
    global _per_doctor_price_id
    if _per_doctor_price_id is None:
        healthcare_client = get_healthcare_client()
        result = healthcare_client.table("subscription_tiers").select(
            "stripe_price_id"
        ).eq("tier_name", "per_doctor").single().execute()
        if result.data:
            _per_doctor_price_id = result.data.get("stripe_price_id")
    return _per_doctor_price_id


async def get_organization_doctor_count(organization_id: str) -> int:
    """Get count of active doctors across all clinics for an organization."""
    # Use main_client for public schema RPC functions
    main_client = get_main_client()

    result = main_client.rpc(
        'get_organization_active_doctor_count',
        {'p_organization_id': organization_id}
    ).execute()

    if result.data is not None:
        return result.data
    return 0


async def execute_doctor_sync(organization_id: str) -> Dict[str, Any]:
    """
    Core sync logic - updates Stripe subscription quantity based on active doctor count.

    SAFETY: Only syncs for 'per_doctor' tier subscriptions.
    Legacy tier customers (starter, standard, advanced, enterprise) are NOT modified.

    Returns:
        Dict with status, doctor_count, and any relevant messages
    """
    if not stripe.api_key:
        return {"status": "error", "message": "Stripe is not configured"}

    core_client = get_core_client()
    healthcare_client = get_healthcare_client()

    # Get organization's Stripe customer ID
    org_result = core_client.table("organizations").select(
        "id, stripe_customer_id"
    ).eq("id", organization_id).single().execute()

    if not org_result.data or not org_result.data.get("stripe_customer_id"):
        return {"status": "no_subscription", "message": "Organization has no Stripe customer"}

    customer_id = org_result.data["stripe_customer_id"]

    # Check organization_subscriptions table first (new authoritative source)
    org_sub_result = healthcare_client.table("organization_subscriptions").select(
        "tier, status, stripe_subscription_id, stripe_price_id"
    ).eq("organization_id", organization_id).single().execute()

    # CRITICAL SAFETY CHECK: Only sync for per_doctor tier
    if org_sub_result.data:
        current_tier = org_sub_result.data.get("tier")
        if current_tier != "per_doctor":
            logger.info(f"Skipping sync for org {organization_id}: on legacy tier '{current_tier}'")
            return {
                "status": "skipped",
                "reason": "legacy_tier",
                "tier": current_tier,
                "message": f"Organization is on '{current_tier}' tier, not per-doctor billing"
            }

    # Get active subscriptions (include trialing to prevent duplicates)
    subscriptions = stripe.Subscription.list(
        customer=customer_id,
        limit=10  # Get more to filter properly
    )

    # Filter to active or trialing subscriptions
    active_subs = [
        s for s in subscriptions.data
        if s.status in ('active', 'trialing', 'past_due')
    ]

    if not active_subs:
        return {"status": "no_subscription", "message": "No active subscription found"}

    subscription = active_subs[0]

    # SAFETY: Find the correct subscription item by price ID, not index[0]
    per_doctor_price_id = await get_per_doctor_price_id()
    subscription_item = None

    for item in subscription["items"]["data"]:
        if item.get("price", {}).get("id") == per_doctor_price_id:
            subscription_item = item
            break

    if not subscription_item:
        # This subscription doesn't use per-doctor pricing
        logger.info(f"Subscription {subscription.id} doesn't use per-doctor price, skipping")
        return {
            "status": "skipped",
            "reason": "wrong_price",
            "message": "Subscription is not on per-doctor pricing"
        }

    # Get current doctor count
    doctor_count = await get_organization_doctor_count(organization_id)

    # Enforce minimum of 1 doctor for billing
    billable_count = max(1, doctor_count)

    current_quantity = subscription_item.get("quantity", 0)

    # Skip if no change needed
    if current_quantity == billable_count:
        return {
            "status": "no_change",
            "doctor_count": doctor_count,
            "billable_count": billable_count
        }

    try:
        # Update Stripe subscription quantity
        # Use 'create_prorations' (default) instead of 'always_invoice' to avoid invoice spam
        stripe.SubscriptionItem.modify(
            subscription_item["id"],
            quantity=billable_count,
            proration_behavior='create_prorations'  # Prorations applied to next invoice
        )

        # Update organization_subscriptions cache
        healthcare_client.table("organization_subscriptions").update({
            "doctor_count_cached": doctor_count,
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("organization_id", organization_id).execute()

        logger.info(
            f"Synced doctor count for org {organization_id}: "
            f"{current_quantity} -> {billable_count} (actual: {doctor_count})"
        )

        return {
            "status": "success",
            "doctor_count": doctor_count,
            "billable_count": billable_count,
            "previous_quantity": current_quantity,
            "subscription_id": subscription.id
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error syncing doctor count for org {organization_id}: {e}")
        return {"status": "error", "message": str(e)}


async def sync_doctor_count_for_clinic(clinic_id: str) -> Dict[str, Any]:
    """
    Convenience wrapper that takes a clinic_id and syncs the parent org.
    Used by doctor change hooks.
    """
    healthcare_client = get_healthcare_client()

    # Get organization_id for this clinic
    clinic_result = healthcare_client.table('clinics').select(
        'organization_id'
    ).eq('id', clinic_id).single().execute()

    if not clinic_result.data:
        logger.warning(f"Clinic {clinic_id} not found")
        return {"status": "error", "message": f"Clinic {clinic_id} not found"}

    organization_id = clinic_result.data['organization_id']
    return await execute_doctor_sync(organization_id)
