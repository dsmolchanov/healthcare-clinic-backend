"""
Billing Routes - Stripe Integration
Handles subscription checkout, customer portal, and webhook processing
"""

import os
import logging
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from pydantic import BaseModel
import stripe

from app.database import get_core_client, get_healthcare_client
from app.services.billing_sync_service import (
    execute_doctor_sync,
    get_organization_doctor_count,
    sync_doctor_count_for_clinic
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])

# Initialize Stripe with secret key
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")


# ==============================================================================
# Request/Response Models
# ==============================================================================

class CheckoutSessionRequest(BaseModel):
    tier_name: str = "per_doctor"  # Default to per_doctor, but accept legacy tiers
    return_url: str
    cancel_url: str
    organization_id: Optional[str] = None  # If not provided, get from auth
    specialist_count: Optional[int] = None  # Keep for backwards compatibility with legacy tiers


class CheckoutSessionResponse(BaseModel):
    url: str
    session_id: str


class PortalSessionRequest(BaseModel):
    return_url: str
    organization_id: Optional[str] = None


class PortalSessionResponse(BaseModel):
    url: str


class SubscriptionStatus(BaseModel):
    tier: str
    status: str
    current_period_end: Optional[str] = None
    trial_ends_at: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    doctor_count: int = 0
    price_per_doctor: float = 29.0
    monthly_total: float = 0.0
    is_per_doctor_billing: bool = False  # Helps frontend show appropriate UI


# ==============================================================================
# Helper Functions
# ==============================================================================

async def get_or_create_stripe_customer(organization_id: str) -> str:
    """Get existing Stripe customer ID or create a new customer."""
    core_client = get_core_client()

    # Get organization from core schema
    org_result = core_client.table("organizations").select(
        "id, name, billing_email, stripe_customer_id"
    ).eq("id", organization_id).single().execute()

    if not org_result.data:
        raise HTTPException(status_code=404, detail="Organization not found")

    org = org_result.data

    # If already has Stripe customer, return it
    if org.get("stripe_customer_id"):
        return org["stripe_customer_id"]

    # Create new Stripe customer
    customer = stripe.Customer.create(
        email=org.get("billing_email"),
        name=org.get("name"),
        metadata={
            "organization_id": organization_id,
            "source": "plaintalk_healthcare"
        }
    )

    # Save customer ID to organization
    core_client.table("organizations").update({
        "stripe_customer_id": customer.id
    }).eq("id", organization_id).execute()

    logger.info(f"Created Stripe customer {customer.id} for org {organization_id}")
    return customer.id


async def get_tier_price_id(tier_name: str) -> tuple[str, str]:
    """Get Stripe price ID for a tier. Returns (price_id, product_id)."""
    healthcare_client = get_healthcare_client()

    tier_result = healthcare_client.table("subscription_tiers").select(
        "stripe_product_id, stripe_price_id"
    ).eq("tier_name", tier_name).single().execute()

    if not tier_result.data:
        raise HTTPException(status_code=404, detail=f"Tier '{tier_name}' not found")

    tier = tier_result.data

    if not tier.get("stripe_price_id"):
        raise HTTPException(
            status_code=400,
            detail=f"Tier '{tier_name}' is not configured for Stripe billing. Please contact support."
        )

    return tier["stripe_price_id"], tier.get("stripe_product_id")


async def log_billing_event(
    organization_id: str,
    event_type: str,
    stripe_event_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
    stripe_invoice_id: Optional[str] = None,
    stripe_customer_id: Optional[str] = None,
    amount_cents: Optional[int] = None,
    currency: str = "usd",
    status: Optional[str] = None,
    metadata: Optional[dict] = None,
    clinic_id: Optional[str] = None
):
    """Log a billing event for audit trail."""
    try:
        healthcare_client = get_healthcare_client()
        healthcare_client.table("billing_events").insert({
            "organization_id": organization_id,
            "clinic_id": clinic_id,
            "event_type": event_type,
            "stripe_event_id": stripe_event_id,
            "stripe_subscription_id": stripe_subscription_id,
            "stripe_invoice_id": stripe_invoice_id,
            "stripe_customer_id": stripe_customer_id,
            "amount_cents": amount_cents,
            "currency": currency,
            "status": status,
            "metadata": metadata or {}
        }).execute()
    except Exception as e:
        # Don't fail on logging errors, just log them
        logger.error(f"Failed to log billing event: {e}")


# ==============================================================================
# Endpoints
# ==============================================================================

@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(request: CheckoutSessionRequest):
    """
    Create a Stripe Checkout session for subscription purchase.

    For per_doctor tier: Doctor count is automatically calculated from the database.
    For legacy tiers: Uses provided specialist_count or defaults to 1.
    """
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe is not configured")

    if not request.organization_id:
        raise HTTPException(status_code=400, detail="organization_id is required")

    try:
        # Get or create Stripe customer
        customer_id = await get_or_create_stripe_customer(request.organization_id)

        # Get price ID for requested tier
        price_id, _ = await get_tier_price_id(request.tier_name)

        # Check if customer already has an active or trialing subscription
        # IMPORTANT: Include 'trialing' to prevent duplicate subscriptions
        existing_subs = stripe.Subscription.list(customer=customer_id, limit=10)
        active_or_trialing = [
            s for s in existing_subs.data
            if s.status in ('active', 'trialing', 'past_due', 'incomplete')
        ]
        if active_or_trialing:
            raise HTTPException(
                status_code=400,
                detail="You already have an active subscription. Use 'Manage Billing' to update."
            )

        # Determine quantity based on tier type
        if request.tier_name == "per_doctor":
            # Auto-calculate doctor count for per-doctor tier
            doctor_count = await get_organization_doctor_count(request.organization_id)

            # Require at least 1 active doctor to subscribe
            if doctor_count < 1:
                raise HTTPException(
                    status_code=400,
                    detail="You must have at least 1 active doctor to subscribe. Please add a doctor first."
                )

            quantity = doctor_count  # No max(1, ...) needed - we check above
            trial_days = 15
        else:
            # Legacy tier - use provided specialist_count or default to 1
            quantity = request.specialist_count or 1
            trial_days = 14  # Legacy tiers use 14-day trial

        # Create Checkout Session
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{
                "price": price_id,
                "quantity": quantity
            }],
            success_url=request.return_url + "?session_id={CHECKOUT_SESSION_ID}&status=success",
            cancel_url=request.cancel_url + "?status=cancelled",
            subscription_data={
                "trial_period_days": trial_days,
                "metadata": {
                    "organization_id": request.organization_id,
                    "tier_name": request.tier_name,
                    "doctor_count": str(quantity)
                }
            },
            metadata={
                "organization_id": request.organization_id,
                "tier_name": request.tier_name,
                "doctor_count": str(quantity)
            },
            allow_promotion_codes=True,
            billing_address_collection="required"
        )

        logger.info(f"Created checkout session {session.id} for org {request.organization_id}, tier {request.tier_name}, quantity {quantity}")

        return CheckoutSessionResponse(
            url=session.url or "",
            session_id=session.id
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating checkout session: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/create-portal-session", response_model=PortalSessionResponse)
async def create_portal_session(request: PortalSessionRequest):
    """
    Create a Stripe Customer Portal session for subscription management.

    The portal allows users to:
    - Update payment methods
    - View invoices
    - Upgrade/downgrade subscription
    - Cancel subscription
    """
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe is not configured")

    if not request.organization_id:
        raise HTTPException(status_code=400, detail="organization_id is required")

    try:
        # Get organization's Stripe customer ID
        core_client = get_core_client()
        org_result = core_client.table("organizations").select(
            "stripe_customer_id"
        ).eq("id", request.organization_id).single().execute()

        if not org_result.data:
            raise HTTPException(status_code=404, detail="Organization not found")

        customer_id = org_result.data.get("stripe_customer_id")

        if not customer_id:
            raise HTTPException(
                status_code=400,
                detail="No billing account found. Please subscribe first."
            )

        # Create portal session
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=request.return_url
        )

        logger.info(f"Created portal session for customer {customer_id}")

        return PortalSessionResponse(url=session.url)

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating portal session: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/subscription-status", response_model=SubscriptionStatus)
async def get_subscription_status_endpoint(organization_id: str):
    """Get current subscription status for an organization."""
    healthcare_client = get_healthcare_client()

    # Try organization_subscriptions first (new table)
    org_sub_result = healthcare_client.table("organization_subscriptions").select(
        "tier, status, current_period_end, trial_ends_at, stripe_subscription_id, doctor_count_cached, price_per_doctor"
    ).eq("organization_id", organization_id).limit(1).execute()

    if org_sub_result.data and len(org_sub_result.data) > 0:
        sub = org_sub_result.data[0]  # Get first result
        doctor_count = sub.get("doctor_count_cached", 0)
        price_per_doctor = sub.get("price_per_doctor", 29.0)
        tier = sub.get("tier", "per_doctor")

        return SubscriptionStatus(
            tier=tier,
            status=sub.get("status", "inactive"),
            current_period_end=sub.get("current_period_end"),
            trial_ends_at=sub.get("trial_ends_at"),
            stripe_subscription_id=sub.get("stripe_subscription_id"),
            doctor_count=doctor_count,
            price_per_doctor=price_per_doctor,
            monthly_total=doctor_count * price_per_doctor if tier == "per_doctor" else 0,
            is_per_doctor_billing=(tier == "per_doctor")
        )

    # Fallback to clinic_subscriptions for legacy data
    clinic_result = healthcare_client.table("clinics").select(
        "id"
    ).eq("organization_id", organization_id).limit(1).execute()

    if not clinic_result.data:
        # No clinic yet - show preview of per-doctor pricing
        doctor_count = await get_organization_doctor_count(organization_id)
        return SubscriptionStatus(
            tier="per_doctor",
            status="inactive",
            doctor_count=doctor_count,
            price_per_doctor=29.0,
            monthly_total=max(1, doctor_count) * 29.0,
            is_per_doctor_billing=True
        )

    clinic_id = clinic_result.data[0]["id"]

    sub_result = healthcare_client.table("clinic_subscriptions").select(
        "tier, status, current_period_end, trial_ends_at, stripe_subscription_id"
    ).eq("clinic_id", clinic_id).limit(1).execute()

    if not sub_result.data or len(sub_result.data) == 0:
        doctor_count = await get_organization_doctor_count(organization_id)
        return SubscriptionStatus(
            tier="per_doctor",
            status="inactive",
            doctor_count=doctor_count,
            price_per_doctor=29.0,
            monthly_total=max(1, doctor_count) * 29.0,
            is_per_doctor_billing=True
        )

    sub = sub_result.data[0]
    tier = sub.get("tier", "starter")

    # For legacy tiers, don't show per-doctor pricing
    if tier != "per_doctor":
        return SubscriptionStatus(
            tier=tier,
            status=sub.get("status", "inactive"),
            current_period_end=sub.get("current_period_end"),
            trial_ends_at=sub.get("trial_ends_at"),
            stripe_subscription_id=sub.get("stripe_subscription_id"),
            doctor_count=0,
            price_per_doctor=0,
            monthly_total=0,
            is_per_doctor_billing=False
        )

    doctor_count = await get_organization_doctor_count(organization_id)
    return SubscriptionStatus(
        tier=tier,
        status=sub.get("status", "inactive"),
        current_period_end=sub.get("current_period_end"),
        trial_ends_at=sub.get("trial_ends_at"),
        stripe_subscription_id=sub.get("stripe_subscription_id"),
        doctor_count=doctor_count,
        price_per_doctor=29.0,
        monthly_total=doctor_count * 29.0,
        is_per_doctor_billing=True
    )


# ==============================================================================
# Doctor Sync Endpoints
# ==============================================================================

@router.post("/sync-doctor-count")
async def sync_doctor_count(organization_id: str):
    """
    Sync the current doctor count to Stripe subscription.
    Called when doctors are added, removed, or deactivated.

    SAFETY: Only syncs for 'per_doctor' tier subscriptions.
    """
    result = await execute_doctor_sync(organization_id)

    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))

    # Log billing event for audit trail
    if result.get("status") == "success":
        await log_billing_event(
            organization_id=organization_id,
            event_type="doctor_count_synced",
            stripe_subscription_id=result.get("subscription_id"),
            metadata={
                "doctor_count": result.get("doctor_count"),
                "billable_count": result.get("billable_count"),
                "previous_quantity": result.get("previous_quantity")
            }
        )

    return result


@router.post("/doctor-changed")
async def doctor_changed_webhook(clinic_id: str):
    """
    Webhook called after doctor add/remove/deactivate operations.
    Triggers billing sync for the clinic's organization.
    """
    result = await sync_doctor_count_for_clinic(clinic_id)
    return result


# ==============================================================================
# Webhook Handler
# ==============================================================================

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    """
    Handle Stripe webhook events.

    This endpoint receives events from Stripe and updates our database accordingly.
    Events are verified using the webhook signature.
    """
    if not STRIPE_WEBHOOK_SECRET:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=500, detail="Webhook not configured")

    # Get raw body for signature verification
    body = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            body, stripe_signature, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid webhook signature: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]

    logger.info(f"Received Stripe webhook: {event_type}")

    try:
        # Handle different event types
        if event_type == "checkout.session.completed":
            await handle_checkout_completed(event)

        elif event_type == "customer.subscription.updated":
            await handle_subscription_updated(event)

        elif event_type == "customer.subscription.deleted":
            await handle_subscription_deleted(event)

        elif event_type == "invoice.payment_succeeded":
            await handle_invoice_paid(event)

        elif event_type == "invoice.payment_failed":
            await handle_invoice_failed(event)

        else:
            logger.info(f"Unhandled event type: {event_type}")

        return {"status": "success"}

    except Exception as e:
        logger.error(f"Error processing webhook {event_type}: {e}")
        # Return 200 to prevent Stripe from retrying
        # but log the error for investigation
        return {"status": "error", "message": str(e)}


async def handle_checkout_completed(event):
    """Handle successful checkout - activate subscription."""
    session = event["data"]["object"]

    organization_id = session.get("metadata", {}).get("organization_id")
    tier_name = session.get("metadata", {}).get("tier_name", "per_doctor")
    subscription_id = session.get("subscription")
    customer_id = session.get("customer")

    if not organization_id:
        logger.error(f"Missing organization_id in checkout session: {session.get('id')}")
        return

    logger.info(f"Checkout completed for org {organization_id}, tier {tier_name}")

    # Get subscription details from Stripe
    subscription = stripe.Subscription.retrieve(subscription_id)

    healthcare_client = get_healthcare_client()
    core_client = get_core_client()

    # Get the price ID from the subscription
    price_id = None
    if subscription.get("items", {}).get("data"):
        price_id = subscription["items"]["data"][0].get("price", {}).get("id")

    # Get current doctor count
    doctor_count = await get_organization_doctor_count(organization_id)

    # Determine status
    sub_status = "trialing" if subscription.status == "trialing" else "active"

    # Create/update organization_subscriptions record (new authoritative source)
    healthcare_client.table("organization_subscriptions").upsert({
        "organization_id": organization_id,
        "tier": tier_name,
        "status": sub_status,
        "stripe_subscription_id": subscription_id,
        "stripe_price_id": price_id,
        "doctor_count_cached": doctor_count,
        "trial_ends_at": datetime.fromtimestamp(
            subscription.trial_end, tz=timezone.utc
        ).isoformat() if subscription.trial_end else None,
        "current_period_start": datetime.fromtimestamp(
            subscription.current_period_start, tz=timezone.utc
        ).isoformat(),
        "current_period_end": datetime.fromtimestamp(
            subscription.current_period_end, tz=timezone.utc
        ).isoformat(),
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }, on_conflict="organization_id").execute()

    # Also update clinic_subscriptions for backwards compatibility
    clinics_result = healthcare_client.table("clinics").select(
        "id"
    ).eq("organization_id", organization_id).execute()

    for clinic in clinics_result.data or []:
        clinic_id = clinic["id"]
        healthcare_client.table("clinic_subscriptions").upsert({
            "clinic_id": clinic_id,
            "tier": tier_name,
            "status": sub_status,
            "stripe_subscription_id": subscription_id,
            "trial_ends_at": datetime.fromtimestamp(
                subscription.trial_end, tz=timezone.utc
            ).isoformat() if subscription.trial_end else None,
            "current_period_start": datetime.fromtimestamp(
                subscription.current_period_start, tz=timezone.utc
            ).isoformat(),
            "current_period_end": datetime.fromtimestamp(
                subscription.current_period_end, tz=timezone.utc
            ).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, on_conflict="clinic_id").execute()

    # Update organization's subscription_tier
    core_client.table("organizations").update({
        "subscription_tier": tier_name
    }).eq("id", organization_id).execute()

    # Log billing event
    await log_billing_event(
        organization_id=organization_id,
        event_type="checkout.session.completed",
        stripe_event_id=event["id"],
        stripe_subscription_id=subscription_id,
        stripe_customer_id=customer_id,
        status="success",
        metadata={
            "tier_name": tier_name,
            "session_id": session.get("id"),
            "doctor_count": doctor_count
        }
    )


async def handle_subscription_updated(event):
    """Handle subscription updates (upgrades/downgrades, trial-to-active, etc)."""
    subscription = event["data"]["object"]
    subscription_id = subscription.get("id")
    customer_id = subscription.get("customer")

    core_client = get_core_client()
    healthcare_client = get_healthcare_client()

    # Get organization by Stripe customer ID
    org_result = core_client.table("organizations").select(
        "id"
    ).eq("stripe_customer_id", customer_id).single().execute()

    if not org_result.data:
        logger.error(f"No organization found for customer {customer_id}")
        return

    organization_id = org_result.data["id"]

    # Get the tier from the subscription items
    items = subscription.get("items", {}).get("data", [])
    if not items:
        logger.error(f"No items in subscription {subscription_id}")
        return

    price_id = items[0].get("price", {}).get("id")
    quantity = items[0].get("quantity", 1)

    # Look up tier by price ID
    tier_result = healthcare_client.table("subscription_tiers").select(
        "tier_name"
    ).eq("stripe_price_id", price_id).single().execute()

    if not tier_result.data:
        logger.warning(f"Unknown price ID: {price_id}, defaulting to per_doctor")
        tier_name = "per_doctor"
    else:
        tier_name = tier_result.data["tier_name"]

    # Map Stripe status to our status
    status_map = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "cancelled",
        "unpaid": "suspended",
        "trialing": "trialing",  # Keep consistent with our constraint
        "incomplete": "incomplete"
    }
    status = status_map.get(subscription.get("status"), "inactive")

    # Update organization_subscriptions (new authoritative source)
    healthcare_client.table("organization_subscriptions").upsert({
        "organization_id": organization_id,
        "tier": tier_name,
        "status": status,
        "stripe_subscription_id": subscription_id,
        "stripe_price_id": price_id,
        "doctor_count_cached": quantity,  # Update cached count from Stripe quantity
        "trial_ends_at": datetime.fromtimestamp(
            subscription.trial_end, tz=timezone.utc
        ).isoformat() if subscription.get("trial_end") else None,
        "current_period_start": datetime.fromtimestamp(
            subscription.current_period_start, tz=timezone.utc
        ).isoformat(),
        "current_period_end": datetime.fromtimestamp(
            subscription.current_period_end, tz=timezone.utc
        ).isoformat(),
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }, on_conflict="organization_id").execute()

    # Also update clinic_subscriptions for backwards compatibility
    clinics_result = healthcare_client.table("clinics").select(
        "id"
    ).eq("organization_id", organization_id).execute()

    for clinic in clinics_result.data or []:
        healthcare_client.table("clinic_subscriptions").update({
            "tier": tier_name,
            "status": status,
            "current_period_start": datetime.fromtimestamp(
                subscription.current_period_start, tz=timezone.utc
            ).isoformat(),
            "current_period_end": datetime.fromtimestamp(
                subscription.current_period_end, tz=timezone.utc
            ).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("stripe_subscription_id", subscription_id).execute()

    # Update organization tier
    core_client.table("organizations").update({
        "subscription_tier": tier_name
    }).eq("id", organization_id).execute()

    logger.info(f"Updated subscription {subscription_id} to tier {tier_name}, status {status}, quantity {quantity}")

    # Log billing event
    await log_billing_event(
        organization_id=organization_id,
        event_type="customer.subscription.updated",
        stripe_event_id=event["id"],
        stripe_subscription_id=subscription_id,
        stripe_customer_id=customer_id,
        status=status,
        metadata={"tier_name": tier_name}
    )


async def handle_subscription_deleted(event):
    """Handle subscription cancellation."""
    subscription = event["data"]["object"]
    subscription_id = subscription.get("id")
    customer_id = subscription.get("customer")

    core_client = get_core_client()
    healthcare_client = get_healthcare_client()

    # Get organization by Stripe customer ID
    org_result = core_client.table("organizations").select(
        "id"
    ).eq("stripe_customer_id", customer_id).single().execute()

    if not org_result.data:
        logger.error(f"No organization found for customer {customer_id}")
        return

    organization_id = org_result.data["id"]

    # Mark all clinic subscriptions as cancelled
    healthcare_client.table("clinic_subscriptions").update({
        "status": "cancelled",
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).eq("stripe_subscription_id", subscription_id).execute()

    # Downgrade organization to starter (free tier)
    core_client.table("organizations").update({
        "subscription_tier": "starter"
    }).eq("id", organization_id).execute()

    logger.info(f"Cancelled subscription {subscription_id} for org {organization_id}")

    await log_billing_event(
        organization_id=organization_id,
        event_type="customer.subscription.deleted",
        stripe_event_id=event["id"],
        stripe_subscription_id=subscription_id,
        stripe_customer_id=customer_id,
        status="cancelled"
    )


async def handle_invoice_paid(event):
    """Handle successful invoice payment - extend subscription period."""
    invoice = event["data"]["object"]
    subscription_id = invoice.get("subscription")
    customer_id = invoice.get("customer")

    if not subscription_id:
        # One-time payment, not subscription
        return

    core_client = get_core_client()
    healthcare_client = get_healthcare_client()

    # Get organization
    org_result = core_client.table("organizations").select(
        "id"
    ).eq("stripe_customer_id", customer_id).single().execute()

    if not org_result.data:
        return

    organization_id = org_result.data["id"]

    # Get updated subscription period from Stripe
    subscription = stripe.Subscription.retrieve(subscription_id)

    # Update clinic subscriptions with new period
    healthcare_client.table("clinic_subscriptions").update({
        "status": "active",
        "current_period_end": datetime.fromtimestamp(
            subscription.current_period_end, tz=timezone.utc
        ).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).eq("stripe_subscription_id", subscription_id).execute()

    logger.info(f"Invoice paid for subscription {subscription_id}")

    await log_billing_event(
        organization_id=organization_id,
        event_type="invoice.payment_succeeded",
        stripe_event_id=event["id"],
        stripe_subscription_id=subscription_id,
        stripe_invoice_id=invoice.get("id"),
        stripe_customer_id=customer_id,
        amount_cents=invoice.get("amount_paid"),
        currency=invoice.get("currency", "usd"),
        status="paid"
    )


async def handle_invoice_failed(event):
    """Handle failed invoice payment - mark subscription as past_due."""
    invoice = event["data"]["object"]
    subscription_id = invoice.get("subscription")
    customer_id = invoice.get("customer")

    if not subscription_id:
        return

    core_client = get_core_client()
    healthcare_client = get_healthcare_client()

    # Get organization
    org_result = core_client.table("organizations").select(
        "id"
    ).eq("stripe_customer_id", customer_id).single().execute()

    if not org_result.data:
        return

    organization_id = org_result.data["id"]

    # Mark subscription as past_due
    healthcare_client.table("clinic_subscriptions").update({
        "status": "past_due",
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).eq("stripe_subscription_id", subscription_id).execute()

    logger.warning(f"Invoice payment failed for subscription {subscription_id}")

    await log_billing_event(
        organization_id=organization_id,
        event_type="invoice.payment_failed",
        stripe_event_id=event["id"],
        stripe_subscription_id=subscription_id,
        stripe_invoice_id=invoice.get("id"),
        stripe_customer_id=customer_id,
        amount_cents=invoice.get("amount_due"),
        currency=invoice.get("currency", "usd"),
        status="failed"
    )


# ==============================================================================
# Admin Endpoints
# ==============================================================================

@router.post("/admin/run-reconciliation")
async def run_billing_reconciliation():
    """
    Admin endpoint to manually trigger billing reconciliation.
    Useful for debugging or after system maintenance.
    """
    try:
        from app.workers.billing_reconciliation import run_reconciliation_now
        result = await run_reconciliation_now()
        return result
    except Exception as e:
        logger.error(f"Manual reconciliation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Also expose webhook at /api/webhooks/stripe for consistency with other webhooks
webhooks_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

@webhooks_router.post("/stripe")
async def stripe_webhook_alt(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    """Alternate webhook endpoint at /api/webhooks/stripe."""
    return await stripe_webhook(request, stripe_signature)
