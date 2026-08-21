"""
usage.py — tier definitions, token budgets, Groq key rotation, and budget checks.

Tiers:
  free  — teacher+artist only, Groq only, shared daily pool across ALL free users.
  trial — full 6-role team, its own Groq key pool (capped at the SAME daily
          amount as the free pool, per spec), plus NVIDIA + OpenRouter.
          Ends on whichever comes first: daily cap hit repeatedly, total
          7-day token budget exhausted, or 7 calendar days elapsed.
  paid  — full 6-role team, OpenRouter/Venice primary, plan-defined token cap.

This module owns:
  - TIER_LIMITS config (the actual numbers)
  - Groq key rotation (separate pools for free vs trial so trial traffic
    never starves the free pool or vice versa)
  - log_usage() / check_budget() against the `usage_ledger` docdb collection
"""
from __future__ import annotations

import itertools
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("usage")

# ---------------------------------------------------------------------------
# Tier limits
# ---------------------------------------------------------------------------
TIER_LIMITS = {
    "free": {
        # Shared across the whole free-tier user base per day, not per-user.
        "pool_daily_tokens": int(os.environ.get("FREE_POOL_DAILY_TOKENS", 500_000)),
        "per_user_daily_tokens": int(os.environ.get("FREE_USER_DAILY_TOKENS", 40_000)),
    },
    "trial": {
        # Per-user. Groq portion of this is capped at the free tier's
        # per-user daily amount (see groq_daily_cap_for below); NVIDIA +
        # OpenRouter stack on top for the extra 6-role roles.
        "daily_tokens": int(os.environ.get("TRIAL_DAILY_TOKENS", 30_000)),
        "total_tokens": int(os.environ.get("TRIAL_TOTAL_TOKENS", 150_000)),
        "max_days": int(os.environ.get("TRIAL_MAX_DAYS", 7)),
    },
    "paid": {
        # Placeholder until plan tiers are finalized — override per plan.
        "daily_tokens": int(os.environ.get("PAID_DAILY_TOKENS", 500_000)),
        "monthly_tokens": int(os.environ.get("PAID_MONTHLY_TOKENS", 3_000_000)),
    },
}


def groq_daily_cap_for(tier: str) -> int:
    """Trial's Groq usage is capped at the SAME per-user daily amount as free —
    trial's extra value comes from NVIDIA+OpenRouter stacking on top, not a
    bigger Groq allowance."""
    return TIER_LIMITS["free"]["per_user_daily_tokens"]


# ---------------------------------------------------------------------------
# Groq key rotation — separate pools for free vs trial
# ---------------------------------------------------------------------------
def _load_key_pool(prefix: str) -> list[str]:
    """Reads GROQ_API_KEY_1..N (free pool) or GROQ_TRIAL_KEY_1..N (trial pool)
    from env. Falls back to a single GROQ_API_KEY if no numbered keys exist."""
    keys = []
    i = 1
    while True:
        k = os.environ.get(f"{prefix}_{i}", "").strip()
        if not k:
            break
        keys.append(k)
        i += 1
    if not keys:
        single = os.environ.get(prefix, "").strip() or os.environ.get("GROQ_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


_FREE_POOL = _load_key_pool("GROQ_API_KEY")
_TRIAL_POOL = _load_key_pool("GROQ_TRIAL_KEY")
_free_cycle = itertools.cycle(_FREE_POOL) if _FREE_POOL else None
_trial_cycle = itertools.cycle(_TRIAL_POOL) if _TRIAL_POOL else None
print(f"[usage.py] groq key pools loaded: free={len(_FREE_POOL)} trial={len(_TRIAL_POOL)} "
      f"(restart the process after adding/changing Render env vars — pools are read once at import)", flush=True)


def next_groq_key(tier: str) -> Optional[str]:
    """Round-robin key selection from the tier's dedicated Groq pool."""
    if tier == "trial" and _trial_cycle is not None:
        return next(_trial_cycle)
    if _free_cycle is not None:
        return next(_free_cycle)
    return None


def pool_size(tier: str) -> int:
    """Number of Groq keys available in the tier's pool — bounds how many
    times run_stage retries a rate-limited stage by rotating to the next key."""
    if tier == "trial":
        return len(_TRIAL_POOL) or 1
    return len(_FREE_POOL) or 1


# ---------------------------------------------------------------------------
# Usage ledger (backed by docdb's generic `docs` table via db.usage_ledger)
# ---------------------------------------------------------------------------
def _day_key(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d")


async def log_usage(db, user_id: str, tier: str, provider: str, role: str,
                     tokens: int, chain_id: Optional[str] = None) -> None:
    """Append a usage record. One row per call; budgets are computed by
    summing rows, not by maintaining a running counter (simpler, auditable)."""
    entry_id = f"{user_id}:{datetime.now(timezone.utc).isoformat()}:{role}"
    try:
        await db.usage_ledger.insert_one({
            "id": entry_id,
            "user_id": user_id,
            "tier": tier,
            "provider": provider,
            "role": role,
            "tokens": int(tokens or 0),
            "chain_id": chain_id,
            "day": _day_key(),
            "ts": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        logger.warning("log_usage failed (non-fatal): %s", exc)


async def _sum_tokens(db, query: dict) -> int:
    try:
        rows = await db.usage_ledger.find(query).to_list(None)
    except Exception as exc:
        logger.warning("usage ledger query failed: %s", exc)
        return 0
    return sum(int(r.get("tokens", 0) or 0) for r in rows)


async def check_budget(db, user_id: str, tier: str) -> dict:
    """Returns {ok: bool, reason: str|None, remaining_daily: int, remaining_total: int|None}.

    Called BEFORE making an LLM call. If ok=False, the caller must NOT call
    the provider and should surface the throttle/upgrade message instead.
    """
    today = _day_key()

    if tier == "free":
        limits = TIER_LIMITS["free"]
        pool_used_today = await _sum_tokens(db, {"tier": "free", "day": today})
        user_used_today = await _sum_tokens(db, {"tier": "free", "day": today, "user_id": user_id})
        if pool_used_today >= limits["pool_daily_tokens"]:
            return {"ok": False, "reason": "pool_exhausted",
                    "remaining_daily": 0, "remaining_total": None}
        if user_used_today >= limits["per_user_daily_tokens"]:
            return {"ok": False, "reason": "user_daily_cap",
                    "remaining_daily": 0, "remaining_total": None}
        return {"ok": True, "reason": None,
                "remaining_daily": limits["per_user_daily_tokens"] - user_used_today,
                "remaining_total": None}

    if tier == "trial":
        limits = TIER_LIMITS["trial"]
        user_doc = await db.users.find_one({"user_id": user_id})
        trial_started = (user_doc or {}).get("trial_started_at")
        if trial_started:
            started_dt = datetime.fromisoformat(trial_started)
            elapsed_days = (datetime.now(timezone.utc) - started_dt).days
            if elapsed_days >= limits["max_days"]:
                return {"ok": False, "reason": "trial_expired_days",
                        "remaining_daily": 0, "remaining_total": 0}
        total_used = await _sum_tokens(db, {"tier": "trial", "user_id": user_id})
        if total_used >= limits["total_tokens"]:
            return {"ok": False, "reason": "trial_total_exhausted",
                    "remaining_daily": 0, "remaining_total": 0}
        day_used = await _sum_tokens(db, {"tier": "trial", "user_id": user_id, "day": today})
        if day_used >= limits["daily_tokens"]:
            return {"ok": False, "reason": "trial_daily_exhausted",
                    "remaining_daily": 0,
                    "remaining_total": limits["total_tokens"] - total_used}
        return {"ok": True, "reason": None,
                "remaining_daily": limits["daily_tokens"] - day_used,
                "remaining_total": limits["total_tokens"] - total_used}

    if tier == "paid":
        limits = TIER_LIMITS["paid"]
        day_used = await _sum_tokens(db, {"tier": "paid", "user_id": user_id, "day": today})
        if day_used >= limits["daily_tokens"]:
            return {"ok": False, "reason": "paid_daily_exhausted",
                    "remaining_daily": 0, "remaining_total": None}
        return {"ok": True, "reason": None,
                "remaining_daily": limits["daily_tokens"] - day_used,
                "remaining_total": None}

    # Unknown tier — fail closed.
    return {"ok": False, "reason": "unknown_tier", "remaining_daily": 0, "remaining_total": None}


THROTTLE_MESSAGE = (
    "We're experiencing higher demand than usual right now \u2014 "
    "try again later or tomorrow, or upgrade for immediate access."
)
