"""
providers.py — CapCode's model catalog aggregator.

Three OpenAI-compatible providers:
  - NVIDIA NIM         (https://integrate.api.nvidia.com/v1)
  - OpenRouter         (https://openrouter.ai/api/v1)   -- huge catalog
  - Venice             (https://api.venice.ai/api/v1)   -- privacy-first, uncensored

We aggregate available models into a single catalog with metadata:
  {id, provider, display_name, uncensored, coding, price_tier, price_per_1m, roles, free}

Coding models on OpenRouter are curated via a "coding-strong" name-prefix allowlist
(the API doesn't expose a coding tag). All Venice models are surfaced (per user
request), all treated as uncensored-friendly.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger("providers")

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
PROVIDERS = {
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        # Primary key env var. Rotation pool (free-tier + trial-tier) is
        # handled separately in usage.py via GROQ_API_KEY_1..N / GROQ_TRIAL_KEY_1..N.
        "api_key_env": "GROQ_API_KEY",
        "models_url": "https://api.groq.com/openai/v1/models",
        "static_models": [
            {"id": "llama-3.3-70b-versatile", "coding": True, "uncensored": False, "roles": ["teacher", "artist"]},
            {"id": "llama-3.1-8b-instant", "coding": True, "uncensored": False, "roles": ["rater", "corrector"]},
        ],
    },
    "nvidia": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
        "models_url": "https://integrate.api.nvidia.com/v1/models",
        # NVIDIA doesn't expose pricing on /models; pin the known-good models we support.
        "static_models": [
            {"id": "z-ai/glm-5.2", "coding": True, "uncensored": False, "roles": ["teacher"]},
            {"id": "minimaxai/minimax-m3", "coding": True, "uncensored": False, "roles": ["artist"]},
            {"id": "nvidia/llama-3.3-nemotron-super-49b-v1.5", "coding": False, "uncensored": False, "roles": ["rater"]},
            {"id": "deepseek-ai/deepseek-v4-pro", "coding": True, "uncensored": False, "roles": ["teacher", "artist"]},
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "models_url": "https://openrouter.ai/api/v1/models",
    },
    "venice": {
        "label": "Venice",
        "base_url": "https://api.venice.ai/api/v1",
        "api_key_env": "VENICE_API_KEY",
        "models_url": "https://api.venice.ai/api/v1/models",
    },
}


def provider_key(name: str) -> Optional[str]:
    env = PROVIDERS.get(name, {}).get("api_key_env", "")
    return (os.environ.get(env, "") or "").strip() or None


# ---------------------------------------------------------------------------
# Groq key rotation pools
# ---------------------------------------------------------------------------
# Free-tier pool: GROQ_API_KEY_1, GROQ_API_KEY_2, ... (set as many as you have
# in Vercel env vars). Trial-tier pool: GROQ_TRIAL_KEY_1, GROQ_TRIAL_KEY_2, ...
# Kept as SEPARATE pools so trial traffic can never starve free-tier capacity.
_groq_pool_idx = {"free": 0, "trial": 0}


def _load_key_pool(prefix: str) -> list[str]:
    keys = []
    i = 1
    while True:
        v = (os.environ.get(f"{prefix}_{i}", "") or "").strip()
        if not v:
            break
        keys.append(v)
        i += 1
    return keys


def groq_key_pool(pool: str = "free") -> list[str]:
    prefix = "GROQ_API_KEY" if pool == "free" else "GROQ_TRIAL_KEY"
    keys = _load_key_pool(prefix)
    if keys:
        return keys
    # Fall back to a single GROQ_API_KEY if no numbered pool is configured.
    single = provider_key("groq")
    return [single] if single else []


def next_groq_key(pool: str = "free") -> Optional[str]:
    """Round-robin the next key in the given pool ('free' or 'trial')."""
    keys = groq_key_pool(pool)
    if not keys:
        return None
    idx = _groq_pool_idx[pool] % len(keys)
    _groq_pool_idx[pool] = (_groq_pool_idx[pool] + 1) % len(keys)
    return keys[idx]


def provider_available(name: str) -> bool:
    return bool(provider_key(name))


# ---------------------------------------------------------------------------
# Coding-model allowlist for OpenRouter (name-prefix / substring match)
# ---------------------------------------------------------------------------
_OR_CODING_HINTS = [
    "claude", "gpt-5", "gpt-4", "gpt-4o", "o1", "o3", "o4",
    "deepseek", "codestral", "qwen", "qwen3", "qwen-coder", "coder",
    "glm-", "gemini", "grok", "llama-3.3", "llama-3.1-70b", "llama-4",
    "mistral-large", "mixtral", "nova-pro", "yi-",
]


_OR_NON_CODING_HINTS = ["lyria", "whisper", "tts", "voice", "audio", "embed",
                        "vision", "image", "guardrails", "content-safety",
                        "reranker", "moderation", "translate", "-clip"]


def _looks_like_coding(model_id: str) -> bool:
    m = model_id.lower()
    if any(h in m for h in _OR_NON_CODING_HINTS):
        return False
    return any(h in m for h in _OR_CODING_HINTS) or "code" in m or "coder" in m


_UNCENSORED_HINTS = ["uncensored", "abliterated", "dolphin", "openhermes",
                     "nous-hermes", "wizardlm", "airoboros"]


def _looks_uncensored(model_id: str, description: str = "") -> bool:
    blob = f"{model_id} {description}".lower()
    return any(h in blob for h in _UNCENSORED_HINTS)


def _price_tier(price_per_1m: float) -> str:
    if price_per_1m < 1.0:
        return "$"
    if price_per_1m < 8.0:
        return "$$"
    return "$$$"


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------
async def _fetch_openrouter() -> list[dict]:
    key = provider_key("openrouter")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                PROVIDERS["openrouter"]["models_url"],
                headers={"Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            data = r.json().get("data", [])
    except Exception as exc:
        logger.warning("openrouter /models failed: %s", exc)
        return []
    out = []
    for m in data:
        mid = m.get("id") or ""
        if not mid:
            continue
        pricing = m.get("pricing") or {}
        try:
            prompt = float(pricing.get("prompt", 0) or 0) * 1_000_000
            completion = float(pricing.get("completion", 0) or 0) * 1_000_000
        except Exception:
            prompt = completion = 0.0
        avg = (prompt + completion) / 2.0
        is_free = prompt == 0 and completion == 0
        looks_coding = _looks_like_coding(mid)
        is_non_coding = any(h in mid.lower() for h in _OR_NON_CODING_HINTS)
        # Include: any coding model + any free model that isn't obviously non-coding
        if not looks_coding and not (is_free and not is_non_coding):
            continue
        # Treat free-and-not-obviously-non-coding as coding-eligible
        coding_flag = looks_coding or (is_free and not is_non_coding)
        out.append({
            "id": mid,
            "provider": "openrouter",
            "display_name": (m.get("name") or mid),
            "description": (m.get("description") or "")[:280],
            "coding": coding_flag,
            "uncensored": _looks_uncensored(mid, m.get("description") or ""),
            "context_len": m.get("context_length") or 0,
            "price_per_1m": round(avg, 4),
            "price_tier": _price_tier(avg),
            "free": is_free,
            "roles": ["gen", "critic", "rater"],
        })
    return out


async def _fetch_venice() -> list[dict]:
    key = provider_key("venice")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                PROVIDERS["venice"]["models_url"],
                headers={"Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            data = r.json().get("data", [])
    except Exception as exc:
        logger.warning("venice /models failed: %s", exc)
        return []
    out = []
    for m in data:
        mid = m.get("id") or ""
        if not mid:
            continue
        spec = m.get("model_spec") or {}
        pricing = spec.get("pricing") or {}
        try:
            inp = float(pricing.get("input", {}).get("usd", 0) or 0)
            outp = float(pricing.get("output", {}).get("usd", 0) or 0)
        except Exception:
            inp = outp = 0.0
        avg = (inp + outp) / 2.0
        traits = spec.get("traits") or []
        # Venice explicitly flags uncensored/functionCalling/reasoning traits.
        is_uncensored = ("default_uncensored" in traits) or ("uncensored" in mid.lower())
        # User asked for ALL uncensored Venice + coding agents.
        is_coding = any(t in traits for t in ("code", "coding")) or "code" in mid.lower() or "coder" in mid.lower()
        out.append({
            "id": mid,
            "provider": "venice",
            "display_name": (spec.get("name") or mid),
            "description": (spec.get("description") or "")[:280],
            "coding": is_coding,
            "uncensored": is_uncensored,
            "context_len": spec.get("availableContextTokens") or 0,
            "price_per_1m": round(avg, 4),
            "price_tier": _price_tier(avg),
            "free": avg == 0,
            "roles": ["gen", "critic", "rater"],
            "traits": traits,
        })
    # Per user: include ALL Venice uncensored + all coding agents.
    return [m for m in out if m["uncensored"] or m["coding"]] or out


async def _fetch_groq() -> list[dict]:
    # Groq doesn't need a live /models fetch for our purposes — pin the
    # known-good free-tier models, same pattern as NVIDIA's static set.
    out = []
    for spec in PROVIDERS["groq"]["static_models"]:
        out.append({
            "id": spec["id"],
            "provider": "groq",
            "display_name": spec["id"],
            "description": "",
            "coding": spec["coding"],
            "uncensored": spec["uncensored"],
            "context_len": 0,
            "price_per_1m": 0.0,
            "price_tier": "$",
            "free": False,
            "roles": spec["roles"],
        })
    return out


async def _fetch_nvidia() -> list[dict]:
    # NVIDIA doesn't expose pricing/coding tags cleanly. Return our known-good static set.
    out = []
    for spec in PROVIDERS["nvidia"]["static_models"]:
        out.append({
            "id": spec["id"],
            "provider": "nvidia",
            "display_name": spec["id"],
            "description": "",
            "coding": spec["coding"],
            "uncensored": spec["uncensored"],
            "context_len": 0,
            "price_per_1m": 0.0,
            "price_tier": "$",  # NVIDIA's public tier is effectively free for these, but no "free" badge is shown
            "free": False,
            "roles": spec["roles"],
        })
    return out


# ---------------------------------------------------------------------------
# Cache + public API
# ---------------------------------------------------------------------------
_CACHE: dict = {"models": None, "loaded_at": 0.0}
_CACHE_TTL_SEC = 300


async def get_catalog(force: bool = False) -> list[dict]:
    import time as _time

    now = _time.time()
    if not force and _CACHE["models"] is not None and (now - _CACHE["loaded_at"] < _CACHE_TTL_SEC):
        return _CACHE["models"]

    gq, nv, orr, ve = await asyncio.gather(
        _fetch_groq(), _fetch_nvidia(), _fetch_openrouter(), _fetch_venice(),
        return_exceptions=False,
    )
    catalog = [*gq, *nv, *orr, *ve]
    _CACHE["models"] = catalog
    _CACHE["loaded_at"] = now
    return catalog


DEFAULT_ASSIGNMENTS = {
    # CapCode topology: Human -> Teacher -> Artist -> Product -> Rater.
    # Defaults route through OpenRouter (user has credits). NVIDIA is skipped
    # by default because pay-as-you-go is unreliable.
    "teacher": {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"},
    "artist":  {"provider": "openrouter", "model": "anthropic/claude-sonnet-5"},
    "rater":   {"provider": "openrouter", "model": "openai/gpt-3.5-turbo"},
    "architect": {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"},
    "reviewer":  {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
    "corrector": {"provider": "openrouter", "model": "anthropic/claude-sonnet-5"},
    # Fallbacks route through Venice, then OpenRouter.
    "teacher_fallback":   {"provider": "venice",     "model": "qwen3-coder-480b-a35b-instruct-turbo"},
    "artist_fallback":    {"provider": "venice",     "model": "qwen3-coder-480b-a35b-instruct-turbo"},
    "rater_fallback":     {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
    "architect_fallback": {"provider": "venice",     "model": "qwen3-coder-480b-a35b-instruct-turbo"},
    "reviewer_fallback":  {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
    "corrector_fallback": {"provider": "venice",     "model": "qwen3-coder-480b-a35b-instruct-turbo"},
}

# ---------------------------------------------------------------------------
# Tier -> role pipeline + provider routing
# ---------------------------------------------------------------------------
# Free: only teacher+artist ever run, pinned to Groq, no NVIDIA per spec.
# Trial: full 6-role team, token-capped, NVIDIA-only ($2.99 one-time, priced
# to cover the recursion loop's own NVIDIA cost — never touches metered
# OpenRouter/Venice credits).
# Paid: full 6-role team, OpenRouter/Venice primary (unlimited within plan tokens).
TIER_ROLE_SEQUENCE = {
    "free":  ["teacher", "artist"],
    "trial": ["teacher", "architect", "artist", "reviewer", "rater", "corrector"],
    "paid":  ["teacher", "architect", "artist", "reviewer", "rater", "corrector"],
}

TIER_ASSIGNMENTS = {
    "free": {
        "teacher": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
        "artist":  {"provider": "groq", "model": "llama-3.3-70b-versatile"},
        # Rater always runs regardless of tier (needed to score/select the
        # build) — cheapest Groq model to keep it free-tier friendly.
        "rater":   {"provider": "groq", "model": "llama-3.1-8b-instant"},
        # No fallback chain on free — if Groq's pool is exhausted, the
        # tier/budget check in usage.py stops the call before it ever gets here.
    },
    # Trial is a paid ($2.99 one-time) product priced specifically to cover
    # its own recursion-loop cost — kept NVIDIA-only on purpose so trial
    # spend never touches metered OpenRouter/Venice credits. See billing.py
    # / tiers.py for the Stripe one-time-payment wiring.
    "trial": {
        "teacher":   {"provider": "nvidia", "model": "z-ai/glm-5.2"},
        "architect": {"provider": "nvidia", "model": "z-ai/glm-5.2"},
        "artist":    {"provider": "nvidia", "model": "minimaxai/minimax-m3"},
        "reviewer":  {"provider": "nvidia", "model": "nvidia/llama-3.3-nemotron-super-49b-v1.5"},
        "rater":     {"provider": "nvidia", "model": "nvidia/llama-3.3-nemotron-super-49b-v1.5"},
        "corrector": {"provider": "nvidia", "model": "deepseek-ai/deepseek-v4-pro"},
    },
    "paid": DEFAULT_ASSIGNMENTS,  # full OpenRouter/Venice routing as already defined above
}

# Trial's own fallback map — deliberately NVIDIA-only (never falls through to
# a paid OpenRouter/Venice model) so an NVIDIA hiccup can't turn into a
# metered charge for a $2.99 trial customer. Used instead of
# DEFAULT_ASSIGNMENTS's *_fallback entries when tier == "trial".
TRIAL_FALLBACKS = {
    "teacher":   ("nvidia", "deepseek-ai/deepseek-v4-pro"),
    "architect": ("nvidia", "deepseek-ai/deepseek-v4-pro"),
    "artist":    ("nvidia", "deepseek-ai/deepseek-v4-pro"),
    "reviewer":  ("nvidia", "z-ai/glm-5.2"),
    "rater":     ("nvidia", "z-ai/glm-5.2"),
    "corrector": ("nvidia", "z-ai/glm-5.2"),
}


# ---------------------------------------------------------------------------
# Default LLM Teams — 3 one-click presets covering all 6 roles, surfaced in
# Settings as "reset to default team" buttons.
# ---------------------------------------------------------------------------
DEFAULT_TEAMS = {
    "venice": {
        "label": "Venice — Uncensored",
        "description": "All 6 roles routed through Venice's uncensored models. Requires the uncensored-mode waiver.",
        "uncensored": True,
        "assignments": {
            "teacher":   {"provider": "venice", "model": "venice-uncensored"},
            "architect": {"provider": "venice", "model": "qwen3-coder-480b-a35b-instruct-turbo"},
            "artist":    {"provider": "venice", "model": "qwen3-coder-480b-a35b-instruct-turbo"},
            "reviewer":  {"provider": "venice", "model": "venice-uncensored"},
            "rater":     {"provider": "venice", "model": "venice-uncensored"},
            "corrector": {"provider": "venice", "model": "qwen3-coder-480b-a35b-instruct-turbo"},
        },
    },
    "openrouter": {
        "label": "OpenRouter — Best Models",
        "description": "All 6 roles routed through OpenRouter's strongest models, one per role.",
        "uncensored": False,
        "assignments": {r: DEFAULT_ASSIGNMENTS[r] for r in
                         ("teacher", "architect", "artist", "reviewer", "rater", "corrector")},
    },
    "nvidia": {
        "label": "NVIDIA NIM — Trial Team",
        "description": "All 6 roles pinned to NVIDIA NIM models — the same team the 7-day trial uses.",
        "uncensored": False,
        "assignments": dict(TIER_ASSIGNMENTS["trial"]),
    },
}


def assignments_for_tier(tier: str) -> dict:
    return TIER_ASSIGNMENTS.get(tier, TIER_ASSIGNMENTS["free"])


def role_sequence_for_tier(tier: str) -> list[str]:
    return TIER_ROLE_SEQUENCE.get(tier, TIER_ROLE_SEQUENCE["free"])


def openai_client_for(provider: str, api_key: Optional[str] = None):
    """Return an AsyncOpenAI client bound to the named provider, or None if key missing.

    If ``api_key`` is passed (BYOK from user settings) it's used verbatim; otherwise
    the value falls back to the env var registered for the provider.
    """
    key = (api_key or "").strip() or provider_key(provider)
    if not key or provider not in PROVIDERS:
        return None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None
    base = PROVIDERS[provider]["base_url"]
    extra_headers = {}
    if provider == "openrouter":
        # OpenRouter recommends these attribution headers.
        extra_headers = {
            "HTTP-Referer": "https://capcode.emergent.host",
            "X-Title": "CapCode",
        }
    return AsyncOpenAI(
        api_key=key,
        base_url=base,
        default_headers=extra_headers,
        timeout=90.0,
        max_retries=0,
    )
