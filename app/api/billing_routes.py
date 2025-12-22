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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])

# Initialize Stripe with secret key
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")


# ==============================================================================
# Request/Response Models
# ==============================================================================

class CheckoutSessionRequest(BaseModel):
    tier_name: str
    return_url: str
    cancel_url: str
    organization_id: Optional[str] = None  # If not provided, get from auth
    specialist_count: int = 1  # Number of specialists for per-seat pricing


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
    Create a Stripe Checkout session for subscription purchase/upgrade.

    The user will be redirected to Stripe's hosted checkout page.
    """
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe is not configured")

    # For now, require organization_id in request
    # TODO: Get from authenticated user's JWT
    if not request.organization_id:
        raise HTTPException(status_code=400, detail="organization_id is required")

    try:
        # Get or create Stripe customer
        customer_id = await get_or_create_stripe_customer(request.organization_id)

        # Get price ID for requested tier
        price_id, product_id = await get_tier_price_id(request.tier_name)

        # Check if customer already has an active subscription
        # If so, they should use the customer portal for upgrades
        existing_subs = stripe.Subscription.list(customer=customer_id, status='active', limit=1)
        if existing_subs.data:
            raise HTTPException(
                status_code=400,
                detail="You already have an active subscription. Use 'Manage Billing' to upgrade."
            )

        # Use specialist count for quantity (per-seat pricing model)
        quantity = max(1, request.specialist_count)

        # Create Checkout Session
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{
                "price": price_id,
                "quantity": quantity  # Number of specialists
            }],
            success_url=request.return_url + "?session_id={CHECKOUT_SESSION_ID}&status=success",
            cancel_url=request.cancel_url + "?status=cancelled",
            subscription_data={
                "metadata": {
                    "organization_id": request.organization_id,
                    "tier_name": request.tier_name,
                    "specialist_count": str(quantity)
                }
            },
            metadata={
                "organization_id": request.organization_id,
                "tier_name": request.tier_name,
                "specialist_count": str(quantity)
            },
            # Enable automatic tax if configured in Stripe
            # automatic_tax={"enabled": True},
            # Allow promotion codes
            allow_promotion_codes=True,
            # Collect billing address for tax purposes
            billing_address_collection="required"
        )

        logger.info(f"Created checkout session {session.id} for org {request.organization_id}")

        return CheckoutSessionResponse(
            url=session.url,
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
async def get_subscription_status(organization_id: str):
    """Get current subscription status for an organization."""
    healthcare_client = get_healthcare_client()

    # Get the first clinic for this organization
    clinic_result = healthcare_client.table("clinics").select(
        "id"
    ).eq("organization_id", organization_id).limit(1).execute()

    if not clinic_result.data:
        # Return default status if no clinic
        return SubscriptionStatus(
            tier="starter",
            status="inactive"
        )

    clinic_id = clinic_result.data[0]["id"]

    # Get subscription for this clinic
    sub_result = healthcare_client.table("clinic_subscriptions").select(
        "tier, status, current_period_end, trial_ends_at, stripe_subscription_id"
    ).eq("clinic_id", clinic_id).single().execute()

    if not sub_result.data:
        return SubscriptionStatus(
            tier="starter",
            status="inactive"
        )

    sub = sub_result.data
    return SubscriptionStatus(
        tier=sub.get("tier", "starter"),
        status=sub.get("status", "inactive"),
        current_period_end=sub.get("current_period_end"),
        trial_ends_at=sub.get("trial_ends_at"),
        stripe_subscription_id=sub.get("stripe_subscription_id")
    )


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

    # Extract metadata
    organization_id = session.get("metadata", {}).get("organization_id")
    tier_name = session.get("metadata", {}).get("tier_name")
    subscription_id = session.get("subscription")
    customer_id = session.get("customer")

    if not organization_id or not tier_name:
        logger.error(f"Missing metadata in checkout session: {session.get('id')}")
        return

    logger.info(f"Checkout completed for org {organization_id}, tier {tier_name}")

    # Get subscription details from Stripe
    subscription = stripe.Subscription.retrieve(subscription_id)

    healthcare_client = get_healthcare_client()
    core_client = get_core_client()

    # Get all clinics for this organization
    clinics_result = healthcare_client.table("clinics").select(
        "id"
    ).eq("organization_id", organization_id).execute()

    # Update each clinic's subscription
    for clinic in clinics_result.data or []:
        clinic_id = clinic["id"]

        # Upsert clinic subscription
        healthcare_client.table("clinic_subscriptions").upsert({
            "clinic_id": clinic_id,
            "tier": tier_name,
            "status": "active",
            "stripe_subscription_id": subscription_id,
            "current_period_start": datetime.fromtimestamp(
                subscription.current_period_start, tz=timezone.utc
            ).isoformat(),
            "current_period_end": datetime.fromtimestamp(
                subscription.current_period_end, tz=timezone.utc
            ).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, on_conflict="clinic_id").execute()

        logger.info(f"Activated subscription for clinic {clinic_id}")

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
            "session_id": session.get("id")
        }
    )


async def handle_subscription_updated(event):
    """Handle subscription updates (upgrades/downgrades)."""
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

    # Get the new tier from the subscription items
    # Assuming single product subscription
    items = subscription.get("items", {}).get("data", [])
    if not items:
        logger.error(f"No items in subscription {subscription_id}")
        return

    price_id = items[0].get("price", {}).get("id")

    # Look up tier by price ID
    tier_result = healthcare_client.table("subscription_tiers").select(
        "tier_name"
    ).eq("stripe_price_id", price_id).single().execute()

    if not tier_result.data:
        logger.warning(f"Unknown price ID: {price_id}")
        tier_name = "unknown"
    else:
        tier_name = tier_result.data["tier_name"]

    # Map Stripe status to our status
    status_map = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "cancelled",
        "unpaid": "suspended",
        "trialing": "trial"
    }
    status = status_map.get(subscription.get("status"), "inactive")

    # Update all clinic subscriptions for this organization
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

    logger.info(f"Updated subscription {subscription_id} to tier {tier_name}, status {status}")

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


# Also expose webhook at /api/webhooks/stripe for consistency with other webhooks
webhooks_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

@webhooks_router.post("/stripe")
async def stripe_webhook_alt(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    """Alternate webhook endpoint at /api/webhooks/stripe."""
    return await stripe_webhook(request, stripe_signature)
