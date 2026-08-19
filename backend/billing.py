"""
billing.py — $16/mo subscription credit ledger ("Credit Rebirth" model).

Split of the $16 subscription (per product decision):
    API_BUDGET_USD      $8.00  (50%) — covers the user's own build API cost
    RECURSION_FUND_USD  $4.00  (25%) — subsidizes exactly the FIRST refinement
                                        loop of each completed build
    PROFIT_USD          $4.00  (25%) — kept, not spendable

Credits are the unit users actually spend: 1,000 credits = $8.00, and 1
credit = 1 LLM token — this is the same `tokens_used` counter
chain_generator.py already tracks, so no new metering is needed, just a $
label on top of it.

Anything a build spends beyond its allocated pool (api budget for the base
build, recursion fund for the refinement loop) is `user_billed` — logged
here; actually charging a card for the overage is a separate concern
(Stripe invoicing), not built yet.

"Credit Rebirth": when the one free refinement loop produces a build that's
provably better than the original AND yields a reusable Global Logic Module
(chain_generator.py's `refinement.knowledge_module` / "water tank"), the
credits that loop cost are refunded back into the user's API budget.

Works with or without Stripe configured — tiers.py falls back to calling
`start_cycle()` directly (mock subscribe) when STRIPE_SECRET_KEY isn't set,
so this can be tested end-to-end before real payment keys are added.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("billing")

SUB_PRICE_USD = 16.00
API_BUDGET_USD = 8.00
RECURSION_FUND_USD = 4.00
PROFIT_USD = 4.00
CREDITS_PER_DOLLAR = 125  # 1,000 credits = $8.00
API_BUDGET_CREDITS = round(API_BUDGET_USD * CREDITS_PER_DOLLAR)          # 1000
RECURSION_FUND_CREDITS = round(RECURSION_FUND_USD * CREDITS_PER_DOLLAR)  # 500


def credits_to_usd(credits: float) -> float:
    return round(credits / CREDITS_PER_DOLLAR, 4)


async def start_cycle(db, user_id: str) -> dict:
    """Called on (mock or real) subscription purchase — (re)opens a fresh
    30-day pool. Idempotent per call — resets balances, does not accumulate
    across cycles (unused credits don't roll over, matching a standard
    monthly-subscription credit model)."""
    now = datetime.now(timezone.utc)
    cycle = {
        "user_id": user_id,
        "cycle_start": now.isoformat(),
        "cycle_end": (now + timedelta(days=30)).isoformat(),
        "api_budget_credits": API_BUDGET_CREDITS,
        "recursion_fund_credits": RECURSION_FUND_CREDITS,
        "profit_accrued_usd": PROFIT_USD,
        "status": "active",
    }
    await db.billing_cycles.update_one({"user_id": user_id}, {"$set": cycle}, upsert=True)
    logger.info("billing cycle started for user %s", user_id)
    return cycle


async def get_cycle(db, user_id: str) -> Optional[dict]:
    cycle = await db.billing_cycles.find_one({"user_id": user_id, "status": "active"})
    if not cycle:
        return None
    # Lazy renewal — credits refill every 30 days regardless of whether the
    # underlying Stripe subscription bills monthly or annually. Stripe only
    # sends us a webhook on each Stripe invoice (once/year for an annual
    # plan), so the monthly refill has to be driven by our own clock instead
    # of waiting on a Stripe event that won't fire on the right cadence.
    cycle_end = datetime.fromisoformat(cycle["cycle_end"])
    if datetime.now(timezone.utc) >= cycle_end:
        cycle = await start_cycle(db, user_id)
    return cycle


async def cancel_cycle(db, user_id: str) -> None:
    await db.billing_cycles.update_one({"user_id": user_id}, {"$set": {"status": "cancelled"}})


async def apply_chain_billing(db, user_id: str, chain_id: str, refinement: dict) -> Optional[dict]:
    """Runs once per completed chain for a subscribed user. `refinement` is
    the dict chain_generator.py's score stage already produced (ran/
    initial_grade/refined_grade/knowledge_module/water_tank_replenished/
    base_tokens/refinement_tokens). Returns the billing_summary/
    knowledge_audit JSON, or None if the user has no active subscription —
    free/trial/anon builds aren't metered by this ledger."""
    cycle = await get_cycle(db, user_id)
    if not cycle:
        return None

    base_credits = int(refinement.get("base_tokens") or 0)
    loop_credits = int(refinement.get("refinement_tokens") or 0)

    api_budget = cycle["api_budget_credits"]
    fund = cycle["recursion_fund_credits"]

    base_covered = min(base_credits, api_budget)
    fund_applied = min(loop_credits, fund)
    overage_credits = max(0, base_credits - api_budget) + max(0, loop_credits - fund)
    user_billed = credits_to_usd(overage_credits)

    api_budget -= base_covered
    fund -= fund_applied

    seval_found = bool(refinement.get("water_tank_replenished"))
    credit_refunded = 0
    if seval_found:
        credit_refunded = fund_applied  # refund exactly what the winning loop cost
        api_budget += credit_refunded

    await db.billing_cycles.update_one(
        {"user_id": user_id},
        {"$set": {"api_budget_credits": api_budget, "recursion_fund_credits": fund}},
    )

    result = {
        "billing_summary": {
            "sub_cost": SUB_PRICE_USD,
            "fund_applied": credits_to_usd(fund_applied),
            "user_billed": user_billed,
            "net_profit": PROFIT_USD,
        },
        "knowledge_audit": {
            "seval_found": seval_found,
            "credit_refunded": credits_to_usd(credit_refunded),
        },
    }
    await db.chains.update_one({"id": chain_id}, {"$set": {"billing": result}})
    logger.info("chain %s billed for user %s: %s", chain_id, user_id, result)
    return result
