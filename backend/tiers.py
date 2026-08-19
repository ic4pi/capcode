"""
tiers.py — trial and paid tier signup/management endpoints.

Contract these endpoints satisfy (already relied on by server.py's /evolve
and usage.py's check_budget):
  db.users doc fields:
    tier              — "free" | "trial" | "paid"
    trial_started_at  — ISO 8601 UTC, set once, first time tier becomes "trial"
    trial_used        — bool, set True the first time trial starts (one-time only)

Trial is a $2.99 ONE-TIME Stripe charge (not free, not a subscription) —
priced specifically to cover the trial's own NVIDIA-only recursion-loop
cost (see providers.TIER_ASSIGNMENTS["trial"] / TRIAL_FALLBACKS, both
NVIDIA-only end to end so trial spend never touches metered
OpenRouter/Venice credits). Paid ($16/mo, or the same entitlement billed
annually as "paid_annual") is a recurring subscription — annual doesn't
change what a user gets, only how Stripe bills them; the $8/$4/$4 credit
split still refills every 30 days regardless of billing interval (see
billing.get_cycle's lazy renewal).
Both go through the same /checkout endpoint, disambiguated by `plan`.

Checkout/webhook are built against the Stripe API but WILL NOT WORK until
you set STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_ID_PAID,
STRIPE_PRICE_ID_PAID_ANNUAL, STRIPE_PRICE_ID_TRIAL in your environment
(Render), and `pip install stripe` (already in requirements.txt). Until
those are set, /api/tier/checkout only works via the ALLOW_MOCK_BILLING=true
dev/test path — it fails loudly with a 503 in production rather than
silently pretending to work. /api/tier/prices always works (falls back to
DEFAULT_PRICES when Stripe/price IDs aren't configured) so the Pricing page
never shows a blank price.
"""
from __future__ import annotations

import logging
import os
import time as _time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

import billing as _billing

logger = logging.getLogger("tiers")

router = APIRouter(prefix="/api/tier")

TRIAL_PRICE_USD = 2.99

PRICE_ENV_BY_PLAN = {
    "trial": "STRIPE_PRICE_ID_TRIAL",
    "paid": "STRIPE_PRICE_ID_PAID",
    "paid_annual": "STRIPE_PRICE_ID_PAID_ANNUAL",
}

# "paid" also accepts the shorter STRIPE_PRICE_ID as an alias — some Render
# setups (this user's included) named their existing monthly price that way
# before "paid_annual"/"trial" existed and needed their own distinct names.
_LEGACY_PRICE_ENV_ALIAS = {"paid": "STRIPE_PRICE_ID"}


def _price_id_for(plan: str) -> str:
    primary = os.environ.get(PRICE_ENV_BY_PLAN[plan], "").strip()
    if primary:
        return primary
    alias = _LEGACY_PRICE_ENV_ALIAS.get(plan)
    return os.environ.get(alias, "").strip() if alias else ""


# Shown on the Pricing page until/unless a real Stripe price is configured
# for that plan — deliberately NOT hardcoded as the source of truth once
# Stripe IS configured, since the monthly price is expected to drift over
# time (see /prices, which prefers the live Stripe amount when available).
DEFAULT_PRICES = {
    "trial": {"amount": TRIAL_PRICE_USD, "currency": "usd", "interval": "one_time"},
    "paid": {"amount": 16.00, "currency": "usd", "interval": "month"},
    "paid_annual": {"amount": 192.00, "currency": "usd", "interval": "year"},
}

_PRICE_CACHE: dict = {"data": None, "at": 0.0}
_PRICE_CACHE_TTL_SEC = 3600


# ---------------------------------------------------------------------------
# Wiring — server.py passes in its db handle, owner-resolution helper, and
# the app's api router prefix conventions via init(). Avoids a circular
# import (server.py imports this module, this module needs server.py's db).
# ---------------------------------------------------------------------------
_db = None
_owner_from_request = None


def init(db, owner_from_request):
    global _db, _owner_from_request
    _db = db
    _owner_from_request = owner_from_request


async def _require_signed_in(request: Request) -> str:
    owner = await _owner_from_request(request)
    if not owner.get("user_id"):
        raise HTTPException(401, "sign in required for trial/paid tiers")
    return owner["user_id"]


async def _grant_trial(user_id: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    await _db.users.update_one(
        {"user_id": user_id},
        {"$set": {"tier": "trial", "trial_started_at": now, "trial_used": True}},
    )
    return {"tier": "trial", "trial_started_at": now}


@router.get("/prices")
async def get_prices():
    """Public, cached, no auth — the Pricing page's source of truth for what
    to display. Prefers the LIVE amount from Stripe (so the copy never
    drifts from what a card actually gets charged as the price changes over
    time); falls back to DEFAULT_PRICES for any plan without a configured
    Stripe price yet."""
    now = _time.time()
    if _PRICE_CACHE["data"] is not None and (now - _PRICE_CACHE["at"] < _PRICE_CACHE_TTL_SEC):
        return _PRICE_CACHE["data"]

    out = {k: dict(v) for k, v in DEFAULT_PRICES.items()}
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if key:
        try:
            import stripe
            stripe.api_key = key
            for plan in PRICE_ENV_BY_PLAN:
                price_id = _price_id_for(plan)
                if not price_id:
                    continue
                p = stripe.Price.retrieve(price_id)
                if p.get("unit_amount") is not None:
                    out[plan] = {
                        "amount": round(p["unit_amount"] / 100, 2),
                        "currency": p.get("currency", "usd"),
                        "interval": (p.get("recurring") or {}).get("interval") or "one_time",
                    }
        except Exception as exc:
            logger.warning("stripe price fetch failed, serving defaults: %s", exc)

    _PRICE_CACHE["data"] = out
    _PRICE_CACHE["at"] = now
    return out


# ---------------------------------------------------------------------------
# TRIAL — dev/mock only from here on. In production this is a $2.99 Stripe
# one-time charge via POST /checkout {"plan": "trial"}, not a free unlock.
# ---------------------------------------------------------------------------
@router.post("/start-trial")
async def start_trial(request: Request):
    if os.environ.get("ALLOW_MOCK_BILLING", "").strip().lower() != "true":
        raise HTTPException(
            410,
            "the trial now requires a one-time $2.99 payment — "
            "use POST /api/tier/checkout with {\"plan\": \"trial\"}",
        )
    user_id = await _require_signed_in(request)
    user_doc = await _db.users.find_one({"user_id": user_id}) or {}

    if user_doc.get("trial_used"):
        raise HTTPException(409, "trial already used on this account")
    if user_doc.get("tier") == "paid":
        raise HTTPException(409, "already on paid tier")

    return await _grant_trial(user_id)


@router.get("/status")
async def tier_status(request: Request):
    """Read-only status for the frontend to render (current tier, trial days
    remaining if applicable). Does not enforce anything — usage.check_budget()
    in the /evolve path is the actual enforcement."""
    owner = await _owner_from_request(request)
    if not owner.get("user_id"):
        return {"tier": "free", "trial_days_remaining": None}

    user_doc = await _db.users.find_one({"user_id": owner["user_id"]}) or {}
    tier = user_doc.get("tier", "free")
    days_remaining = None
    if tier == "trial" and user_doc.get("trial_started_at"):
        started = datetime.fromisoformat(user_doc["trial_started_at"])
        elapsed = (datetime.now(timezone.utc) - started).days
        days_remaining = max(0, 7 - elapsed)
        if days_remaining == 0:
            # Trial's clock ran out — reflect that in status even though
            # nothing has actively flipped the tier field back yet.
            tier = "free"
    return {"tier": tier, "trial_days_remaining": days_remaining}


# ---------------------------------------------------------------------------
# PAID + TRIAL checkout — Stripe. Skeleton only; needs real Stripe keys
# and real price IDs before this does anything but 500/503.
# ---------------------------------------------------------------------------
def _stripe():
    try:
        import stripe
    except ImportError:
        raise HTTPException(500, "stripe package not installed — add `stripe` to requirements.txt")
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise HTTPException(500, "STRIPE_SECRET_KEY not configured — paid tier checkout is not live yet")
    stripe.api_key = key
    return stripe


@router.post("/checkout")
async def create_checkout_session(request: Request):
    """Body: {"plan": "paid" | "paid_annual" | "trial"} — defaults to "paid"
    for backward compatibility. "paid"/"paid_annual" are the same $16/mo
    entitlement, billed monthly vs annually; "trial" is a one-time charge
    (see PRICE_ENV_BY_PLAN) that unlocks the NVIDIA-only 7-day trial."""
    user_id = await _require_signed_in(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    plan = (body or {}).get("plan", "paid")
    if plan not in ("paid", "paid_annual", "trial"):
        raise HTTPException(400, "plan must be 'paid', 'paid_annual', or 'trial'")

    if plan == "trial":
        user_doc = await _db.users.find_one({"user_id": user_id}) or {}
        if user_doc.get("trial_used"):
            raise HTTPException(409, "trial already used on this account")

    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()

    if not key:
        # SEC-002 fix: mock-subscribe is ONLY available when explicitly opted
        # into via ALLOW_MOCK_BILLING=true (dev/test only). Without that flag
        # this fails closed — a missing Stripe key must never silently grant
        # a free paid-tier or trial upgrade to anyone who's signed in.
        if os.environ.get("ALLOW_MOCK_BILLING", "").strip().lower() != "true":
            raise HTTPException(503, "Billing is not configured yet — try again later.")
        if plan == "trial":
            result = await _grant_trial(user_id)
            return {"checkout_url": None, "mock": True, **result}
        # Grant paid tier + open the $16/mo credit cycle directly so the
        # whole billing flow (fund/profit split, refinement loop subsidy,
        # Credit Rebirth) can be tested end-to-end. Swap to real Stripe
        # checkout below the moment STRIPE_SECRET_KEY is set — no other
        # code changes needed, and this mock path becomes unreachable.
        await _db.users.update_one(
            {"user_id": user_id},
            {"$set": {"tier": "paid", "billing_interval": "annual" if plan == "paid_annual" else "monthly"}},
        )
        cycle = await _billing.start_cycle(_db, user_id)
        return {"checkout_url": None, "mock": True, "tier": "paid", "billing_cycle": cycle}

    stripe = _stripe()
    price_id = _price_id_for(plan)
    if not price_id:
        alias_hint = f" (or the legacy {_LEGACY_PRICE_ENV_ALIAS[plan]})" if plan in _LEGACY_PRICE_ENV_ALIAS else ""
        raise HTTPException(500, f"{PRICE_ENV_BY_PLAN[plan]}{alias_hint} not configured")

    frontend_url = os.environ.get("FRONTEND_URL", "").rstrip("/")
    if not frontend_url:
        raise HTTPException(500, "FRONTEND_URL not configured — needed for Stripe redirect URLs")

    try:
        session = stripe.checkout.Session.create(
            mode="payment" if plan == "trial" else "subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=user_id,
            metadata={"plan": plan, "user_id": user_id},
            success_url=f"{frontend_url}/settings?upgraded=1",
            cancel_url=f"{frontend_url}/settings?upgrade_cancelled=1",
        )
    except Exception as exc:
        logger.error("stripe checkout session creation failed: %s", exc)
        raise HTTPException(502, f"stripe error: {exc}")

    return {"checkout_url": session.url, "mock": False}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe calls this directly (not the frontend). Register this URL
    (<your-domain>/api/tier/webhook) in the Stripe dashboard once you have a
    real account, and set STRIPE_WEBHOOK_SECRET from that same dashboard."""
    stripe = _stripe()
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        raise HTTPException(500, "STRIPE_WEBHOOK_SECRET not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as exc:
        logger.warning("stripe webhook signature verification failed: %s", exc)
        raise HTTPException(400, "invalid webhook signature")

    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if etype == "checkout.session.completed":
        user_id = obj.get("client_reference_id")
        plan = (obj.get("metadata") or {}).get("plan", "paid")
        if user_id and plan == "trial":
            await _grant_trial(user_id)
            logger.info("user %s started NVIDIA-only trial via $2.99 stripe checkout", user_id)
        elif user_id:
            # "paid" and "paid_annual" grant the identical entitlement —
            # annual only changes Stripe's billing interval, never what the
            # user gets (see billing.get_cycle's lazy 30-day renewal, which
            # refills credits monthly either way).
            await _db.users.update_one(
                {"user_id": user_id},
                {"$set": {"tier": "paid", "stripe_customer_id": obj.get("customer"),
                          "billing_interval": "annual" if plan == "paid_annual" else "monthly"}},
            )
            await _billing.start_cycle(_db, user_id)
            logger.info("user %s upgraded to paid (%s) via stripe checkout", user_id, plan)

    elif etype in ("customer.subscription.deleted", "customer.subscription.updated"):
        # On cancellation or a status change away from active, revert to free.
        # Trial is a one-time payment (no subscription object), so this only
        # ever applies to the paid plan.
        status = obj.get("status")
        customer_id = obj.get("customer")
        if customer_id and (etype == "customer.subscription.deleted" or status not in ("active", "trialing")):
            user_doc = await _db.users.find_one({"stripe_customer_id": customer_id})
            if user_doc:
                await _db.users.update_one(
                    {"user_id": user_doc["user_id"]},
                    {"$set": {"tier": "free"}},
                )
                await _billing.cancel_cycle(_db, user_doc["user_id"])
                logger.info("user %s reverted to free — subscription %s", user_doc["user_id"], etype)

    return {"received": True}

