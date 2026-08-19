"""
RECURSIVE.BBS — an application that builds bot-builders.

You push ONE button. The AI (NVIDIA NIM) hands off generation-to-generation
autonomously and emits a chain of complete, runnable code-builder applications.
You download the whole chain as a single .zip.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone
import uuid
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware
import httpx

import chain_generator
import docdb
import executor as _exec
import providers as _providers
import usage as _usage
import tiers as _tiers
import billing as _billing
import edits as _edits
import eval_meta as _eval_meta
import prompt_evolution as _prompt_evo

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("recursive-bbs")

# ---------------------------------------------------------------------------
# Postgres (Neon) via docdb — a small Mongo-look-alike adapter over JSONB.
# ---------------------------------------------------------------------------
POSTGRES_URL = os.environ["POSTGRES_URL"]
db = docdb.db


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Reward(BaseModel):
    helpfulness: float = 0.0
    correctness: float = 0.0
    coherence: float = 0.0
    complexity: float = 0.0
    verbosity: float = 0.0


class GenFile(BaseModel):
    path: str
    content: str


class Generation(BaseModel):
    model_config = ConfigDict(extra="allow")
    gen: int
    name: str = ""
    tagline: str = ""
    philosophy: str = ""
    improvement_note: str = ""
    accent_hex: str = ""
    accent2_hex: str = ""
    weights: dict = Field(default_factory=dict)
    files: list[GenFile] = Field(default_factory=list)
    critic_notes: str = ""
    reward: Reward = Field(default_factory=Reward)
    composite_score: float = 0.0


class Chain(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    target_prompt: str = ""
    depth: int = 1
    status: str = "running"  # running | complete | failed
    fallback_used: bool = False
    verified: bool = False
    generations: list[Generation] = Field(default_factory=list)
    created_at: datetime
    completed_at: Optional[datetime] = None
    venice_consent: Optional[dict] = None
    error: Optional[str] = None
    billing: Optional[dict] = None
    build_manifest: Optional[dict] = None
    built_at: Optional[str] = None
    github: Optional[dict] = None


class EvolveRequest(BaseModel):
    target_prompt: str
    depth: int = 1
    session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _slug(s: str, fallback: str = "chain") -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", s or "").strip("-")
    return s or fallback


def _build_zip(chain_doc: dict, single_gen: Optional[int] = None) -> tuple[io.BytesIO, str]:
    """Build a zip containing the chain (or one specific gen)."""
    gens = chain_doc.get("generations", [])
    chain_slug = f"chain-{chain_doc['id'][:8]}"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if single_gen is not None:
            gen = next((g for g in gens if g.get("gen") == single_gen), None)
            if not gen:
                raise HTTPException(404, f"gen {single_gen} not in chain")
            folder = _slug(gen.get("name") or f"gen-{single_gen}", "gen")
            for f in gen.get("files", []):
                zf.writestr(f"{folder}/{f['path']}", f["content"])
            zf.writestr(f"{folder}/.recursive-bbs.json", json.dumps(_gen_manifest(gen), indent=2))
            zf.writestr(f"{folder}/TEACHER_SPEC.md", _teacher_md(chain_doc, gen))
            zf.writestr(f"{folder}/ARTIST_NOTES.md", _artist_md(gen))
            return buf, f"{folder}.zip"

        # whole chain
        for g in gens:
            sub = f"gen-{g['gen']:02d}-{_slug(g.get('name','gen'), 'gen')}"
            for f in g.get("files", []):
                zf.writestr(f"{chain_slug}/{sub}/{f['path']}", f["content"])
            zf.writestr(
                f"{chain_slug}/{sub}/.recursive-bbs.json",
                json.dumps(_gen_manifest(g), indent=2),
            )
            zf.writestr(f"{chain_slug}/{sub}/TEACHER_SPEC.md", _teacher_md(chain_doc, g))
            zf.writestr(f"{chain_slug}/{sub}/ARTIST_NOTES.md", _artist_md(g))
        # top-level chain manifest
        zf.writestr(f"{chain_slug}/CHAIN.md", _chain_markdown(chain_doc))
        zf.writestr(
            f"{chain_slug}/chain.json",
            json.dumps(
                {
                    "id": chain_doc["id"],
                    "depth": chain_doc["depth"],
                    "created_at": chain_doc.get("created_at"),
                    "generations": [_gen_manifest(g) for g in gens],
                },
                indent=2,
            ),
        )
    buf.seek(0)
    return buf, f"{chain_slug}.zip"


def _gen_manifest(g: dict) -> dict:
    return {
        "gen": g.get("gen"),
        "name": g.get("name"),
        "tagline": g.get("tagline"),
        "philosophy": g.get("philosophy"),
        "stack": g.get("stack"),
        "reward": g.get("reward"),
        "composite_score": g.get("composite_score"),
        "critic_notes": g.get("critic_notes"),
    }


def _teacher_md(chain_doc: dict, g: dict) -> str:
    t = g.get("teacher_spec") or {}
    lines = [
        f"# Teacher Spec — {t.get('name','TeacherSpec')}",
        "",
        f"**Human target:** {chain_doc.get('target_prompt','')}",
        f"**Stack:** {t.get('stack','python-fastapi')}",
        "",
        "## Artist Brief",
        t.get("artist_brief", ""),
        "",
        "## Must Have",
        *[f"- {x}" for x in (t.get("must_have") or [])],
        "",
        "## Should Avoid",
        *[f"- {x}" for x in (t.get("should_avoid") or [])],
    ]
    return "\n".join(lines)


def _artist_md(g: dict) -> str:
    a = g.get("artist_spec") or {}
    return "\n".join([
        f"# Artist Notes — {a.get('name', g.get('name',''))}",
        "",
        f"**Tagline:** {a.get('tagline','')}",
        "",
        "## Philosophy",
        a.get("philosophy", ""),
        "",
        "## Improvement Note",
        a.get("improvement_note", ""),
    ])


def _chain_markdown(chain_doc: dict) -> str:
    lines = [
        f"# Chain {chain_doc['id']}",
        "",
        f"- depth: {chain_doc['depth']}",
        f"- created: {chain_doc.get('created_at')}",
        "- evolved by: NVIDIA NIM (GLM-5.1 generator, MiniMax-M2.7 critic, Nemotron-Super-49B rater)",
        "",
        "Every generation is itself a code-builder application: a FastAPI + HTML app that calls "
        "an LLM and produces source code from input. Run any of them with `bash run.sh`.",
        "",
        "## Generations",
        "",
    ]
    for g in chain_doc.get("generations", []):
        lines += [
            f"### Gen {g['gen']} — {g.get('name','?')}",
            f"*{g.get('tagline','')}*",
            "",
            f"- input style: `{g.get('input_style','?')}`",
            f"- output style: `{g.get('output_style','?')}`",
            f"- composite score: **{g.get('composite_score',0):.2f}**",
            "",
            f"**Philosophy:** {g.get('philosophy','')}",
            "",
            f"**Critic:** {g.get('critic_notes','')}",
            "",
            f"**Reward:** {json.dumps(g.get('reward',{}), separators=(', ', ':'))}",
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="CapCode")
api = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Emergent-managed Google Auth (optional identity — anonymous still works)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Neon Managed Better Auth
# ---------------------------------------------------------------------------
# Frontend logs in through Neon Auth directly (Google OAuth handled by Neon).
# Frontend then sends the resulting JWT as `Authorization: Bearer <jwt>` to
# our backend. We verify the JWT against Neon Auth's JWKS (EdDSA / Ed25519).
NEON_AUTH_URL = os.environ["NEON_AUTH_URL"].rstrip("/")
NEON_JWKS_URL = f"{NEON_AUTH_URL}/.well-known/jwks.json"

import jwt as _pyjwt  # PyJWT
from jwt import PyJWKClient

_jwks_client = PyJWKClient(NEON_JWKS_URL, cache_keys=True, lifespan=300)

# ---------------------------------------------------------------------------
# Neon Auth reverse proxy — the frontend calls THIS (same-origin, via its own
# Vercel `vercel.json` rewrite) instead of Neon's domain directly, so Neon's
# session cookies land as first-party and survive Safari/iOS's cross-site
# cookie blocking. A plain Vercel-level rewrite to Neon's raw URL can't do
# this on its own: Vercel forwards the original Host header unmodified,
# which Neon Auth rejects outright ("INVALID_HOSTNAME") — this proxy issues
# a fresh outbound request per hop instead, so the Host header is always
# correct for Neon's side, while the response still looks first-party to
# the browser.
_neon_proxy_client = httpx.AsyncClient(timeout=30.0)


@api.api_route("/neon-auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def neon_auth_proxy(path: str, request: Request):
    target = f"{NEON_AUTH_URL}/{path}"
    body = await request.body()
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in (
            "host", "content-length", "connection",
            "x-forwarded-host", "x-forwarded-proto", "x-forwarded-for",
            "x-forwarded-port", "forwarded",
        )
    }
    try:
        upstream = await _neon_proxy_client.request(
            request.method, target, params=request.query_params,
            content=body, headers=fwd_headers,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"neon auth upstream unreachable: {exc}")
    resp = Response(content=upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"))
    for key, value in upstream.headers.multi_items():
        lk = key.lower()
        if lk == "set-cookie":
            resp.headers.append(key, value)
        elif lk not in ("content-length", "content-encoding", "transfer-encoding", "connection"):
            resp.headers[key] = value
    return resp


class AuthUser(BaseModel):
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None


async def _read_bearer(request: Request) -> Optional[str]:
    """Return the JWT from Authorization: Bearer header (preferred) or cookie."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("neon_auth_jwt")


def _verify_neon_jwt(token: str) -> Optional[dict]:
    """Verify a Neon Better Auth JWT and return the claims dict, or None on failure."""
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        # Neon Managed Better Auth signs with EdDSA (Ed25519).
        claims = _pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["EdDSA"],
            options={"require": ["sub", "exp"]},
        )
        return claims
    except Exception as exc:
        logger.debug("neon jwt verify failed: %s", exc)
        return None


async def get_current_user(request: Request) -> Optional[AuthUser]:
    """Return the signed-in user or None. Does NOT raise on missing/expired auth."""
    tok = await _read_bearer(request)
    if not tok:
        return None
    claims = _verify_neon_jwt(tok)
    if not claims:
        return None

    user_id = str(claims.get("sub") or "")
    if not user_id:
        return None
    email = claims.get("email") or ""
    name = claims.get("name") or (email.split("@")[0] if email else user_id[:8])
    picture = claims.get("image") or claims.get("picture")

    # Upsert into our own users collection so we can attach chains, settings,
    # etc. to a stable user_id even across future auth changes.
    existing = await db.users.find_one({"user_id": user_id})
    now_iso = datetime.now(timezone.utc).isoformat()
    if existing is None:
        await db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "created_at": now_iso,
            "last_login_at": now_iso,
        })
    else:
        # Refresh mutable fields (email/name/picture) in case Google profile changed.
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"email": email, "name": name, "picture": picture,
                      "last_login_at": now_iso}},
        )

    return AuthUser(user_id=user_id, email=email, name=name, picture=picture)


@api.get("/auth/me")
async def auth_me(request: Request):
    """Return the current signed-in user or 401.
    Frontend calls this after acquiring a JWT from Neon Auth."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "not authenticated")
    return user.model_dump()


@api.get("/auth/config")
async def auth_config():
    """Expose the Neon Auth base URL so the frontend can point its Better Auth
    client at the right server without hard-coding the URL in bundle output."""
    return {"neon_auth_url": NEON_AUTH_URL}


@api.post("/auth/logout")
async def auth_logout(response: Response):
    """Best-effort logout — Better Auth sign-out is handled client-side; this
    endpoint just clears any local server-side artifacts."""
    response.delete_cookie("neon_auth_jwt", path="/")
    return {"ok": True}


@api.get("/")
async def root():
    return {
        "system": "CapCode",
        "version": "2.0.0",
        "providers": {
            name: _providers.provider_available(name)
            for name in _providers.PROVIDERS
        },
        "defaults": _providers.DEFAULT_ASSIGNMENTS,
    }


@api.get("/status")
async def status():
    total = await db.chains.count_documents({})
    return {
        "total_chains": total,
        "providers": {
            name: _providers.provider_available(name)
            for name in _providers.PROVIDERS
        },
    }


@api.get("/billing/status")
async def billing_status(request: Request):
    """Current $16/mo credit cycle for the signed-in user, or null if they
    have no active subscription (free/trial/anon builds aren't metered)."""
    owner = await _owner_from_request(request)
    if not owner.get("user_id"):
        return {"subscribed": False, "cycle": None}
    cycle = await _billing.get_cycle(db, owner["user_id"])
    return {"subscribed": bool(cycle), "cycle": cycle}


@api.get("/providers/models")
async def catalog(refresh: bool = False):
    """Aggregated model catalog across all providers."""
    return {"models": await _providers.get_catalog(force=refresh),
            "defaults": _providers.DEFAULT_ASSIGNMENTS}


class SettingsPayload(BaseModel):
    teacher: Optional[dict] = None
    artist: Optional[dict] = None
    rater: Optional[dict] = None
    keys: Optional[dict] = None  # {openrouter?, venice?, nvidia?}
    github: Optional[dict] = None  # {token?, username?}


@api.get("/settings")
async def get_settings(session_id: str):
    doc = await db.settings.find_one({"session_id": session_id}, {"_id": 0}) or {}
    roles = ("teacher", "artist", "rater")
    # Never leak the actual secret values back to the frontend — just say whether
    # each key is set. Model choices are safe to echo.
    keys = doc.get("keys") or {}
    keys_set = {p: bool((keys.get(p) or "").strip()) for p in ("openrouter", "venice", "nvidia")}
    gh = doc.get("github") or {}
    return {
        "session_id": session_id,
        **{r: doc.get(r) or _providers.DEFAULT_ASSIGNMENTS[r] for r in roles},
        "keys_set": keys_set,
        "github": {"username": gh.get("username", ""), "token_set": bool((gh.get("token") or "").strip())},
    }


@api.post("/settings")
async def save_settings(session_id: str, payload: SettingsPayload):
    update = {}
    if payload.teacher is not None: update["teacher"] = payload.teacher
    if payload.artist is not None:  update["artist"] = payload.artist
    if payload.rater is not None:   update["rater"] = payload.rater
    if payload.keys is not None:
        # Merge with existing keys — empty string means "clear this key".
        existing = (await db.settings.find_one({"session_id": session_id}, {"_id": 0, "keys": 1}) or {}).get("keys") or {}
        for k, v in (payload.keys or {}).items():
            if k in ("openrouter", "venice", "nvidia"):
                v = (v or "").strip()
                if v:
                    existing[k] = v
                else:
                    existing.pop(k, None)
        update["keys"] = existing
    if payload.github is not None:
        existing = (await db.settings.find_one({"session_id": session_id}, {"_id": 0, "github": 1}) or {}).get("github") or {}
        gh = payload.github or {}
        if "username" in gh:
            existing["username"] = (gh.get("username") or "").strip()
        if "token" in gh:
            tok = (gh.get("token") or "").strip()
            if tok:
                existing["token"] = tok
            elif tok == "":
                existing.pop("token", None)
        update["github"] = existing
    await db.settings.update_one(
        {"session_id": session_id},
        {"$set": {"session_id": session_id, **update}},
        upsert=True,
    )
    return await get_settings(session_id)


async def _load_user_keys(session_id: Optional[str]) -> dict:
    """Return {provider: apikey} the user has BYOK'd, or empty dict."""
    if not session_id:
        return {}
    doc = await db.settings.find_one({"session_id": session_id}, {"_id": 0, "keys": 1})
    return (doc or {}).get("keys") or {}


# ---------------------------------------------------------------------------
# Ownership — every chain is owned by either a signed-in user (`user_id`) or
# an anonymous browser (`anon_session_id`). All read/write endpoints filter
# by the caller's identity so nobody sees anyone else's prompts.
# ---------------------------------------------------------------------------
async def _owner_from_request(request: Request) -> dict:
    """Return {user_id, anon} identifying the caller. `anon` comes from the
    `X-Capcode-Session` header (set by the frontend axios interceptor) or a
    `session_id` query param. `user_id` comes from the auth cookie/bearer."""
    user = await get_current_user(request)
    anon = (request.headers.get("x-capcode-session")
            or request.query_params.get("session_id")
            or "").strip() or None
    return {"user_id": (user.user_id if user else None), "anon": anon}


def _owner_filter(owner: dict) -> dict:
    """Mongo query fragment restricting docs to this caller."""
    if owner.get("user_id"):
        return {"user_id": owner["user_id"]}
    if owner.get("anon"):
        return {"anon_session_id": owner["anon"]}
    # No identity at all -> match nothing.
    return {"_id": "__no_owner__"}


def _stamp_owner(doc: dict, owner: dict) -> dict:
    doc = {**doc}
    if owner.get("user_id"):
        doc["user_id"] = owner["user_id"]
    if owner.get("anon"):
        doc["anon_session_id"] = owner["anon"]
    return doc


# ---------------------------------------------------------------------------
# Venice / uncensored-mode consent waiver
# ---------------------------------------------------------------------------
# Every time the user toggles uncensored mode ON, the frontend intercepts with
# a waiver modal and — on explicit "I agree" — hits POST /api/venice/consent.
# We store a fresh record every call (no dedup, no "always accepted" flag).
# The /api/evolve gate uses `_has_recent_venice_consent` to require a fresh
# record for the exact same owner before it will route any role to Venice.
VENICE_CONSENT_WINDOW = timedelta(minutes=30)


class VeniceConsentRequest(BaseModel):
    chain_id: Optional[str] = None  # optional at consent time — often no chain yet


@api.post("/venice/consent")
async def venice_consent(req: VeniceConsentRequest, request: Request):
    """Record a fresh consent-waiver acceptance for uncensored/Venice routing.
    Every toggle-on posts a new record — the backend never reuses a prior one.
    """
    owner = await _owner_from_request(request)
    if not owner.get("user_id") and not owner.get("anon"):
        raise HTTPException(400, "no session identity — sign in or send X-Capcode-Session header")
    consent_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    doc = _stamp_owner({
        "consent_id": consent_id,
        "accepted_at": now_iso,
        "chain_id": (req.chain_id or "").strip() or None,
    }, owner)
    await db.venice_consent.insert_one(doc)
    return {
        "consent_id": consent_id,
        "accepted_at": now_iso,
        "chain_id": doc.get("chain_id"),
        "expires_at": (datetime.now(timezone.utc) + VENICE_CONSENT_WINDOW).isoformat(),
    }


async def _get_recent_venice_consent(owner: dict) -> Optional[dict]:
    """Return the freshest in-window consent record for this owner, or None.
    Consent is owner-scoped: a record from a different user_id or anon
    session id NEVER counts."""
    q = _owner_filter(owner)
    if q.get("_id") == "__no_owner__":
        return None
    docs = await db.venice_consent.find(q).sort("accepted_at", -1).limit(1).to_list(1)
    if not docs:
        return None
    accepted_at = docs[0].get("accepted_at")
    if not accepted_at:
        return None
    try:
        ts = datetime.fromisoformat(accepted_at)
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - ts) > VENICE_CONSENT_WINDOW:
        return None
    return docs[0]


async def _has_recent_venice_consent(owner: dict) -> bool:
    return (await _get_recent_venice_consent(owner)) is not None


# --- Rate limiting -----------------------------------------------------
# In-memory sliding window keyed by caller identity (user_id or anon
# session). Scoped to a single process — fine for the current single-Render-
# instance deployment; would need a shared store (Redis) if this ever scales
# to multiple backend instances.
_rate_buckets: dict[str, list[float]] = {}


def _check_rate_limit(owner: dict, bucket: str, max_calls: int, window_sec: int) -> None:
    key = f"{bucket}:{owner.get('user_id') or owner.get('anon') or 'unknown'}"
    now = time.monotonic()
    hits = [t for t in _rate_buckets.get(key, []) if now - t < window_sec]
    if len(hits) >= max_calls:
        raise HTTPException(429, f"Rate limit exceeded — max {max_calls} per {window_sec}s. Try again shortly.")
    hits.append(now)
    _rate_buckets[key] = hits


@api.post("/evolve", response_model=Chain)
async def evolve(req: EvolveRequest, background_tasks: BackgroundTasks, request: Request):
    """Human types a target. Teacher -> Artist -> Product -> Rater runs in background.
    Returns the chain stub immediately; poll GET /api/chains/{id} until status == 'complete'."""
    if not chain_generator.ENABLED:
        raise HTTPException(503, "No provider API key configured — cannot evolve a chain.")

    target = (req.target_prompt or "").strip()
    if not target:
        raise HTTPException(400, "target_prompt is required — tell CapCode what app to build.")

    # Identify the caller — signed-in user_id OR anonymous session id from
    # the X-Capcode-Session header (fallback: the legacy `session_id` field).
    owner = await _owner_from_request(request)
    if not owner.get("user_id") and not owner.get("anon"):
        # Fall back to the body-level session_id so old clients keep working.
        owner["anon"] = (req.session_id or "").strip() or None
    if not owner.get("user_id") and not owner.get("anon"):
        raise HTTPException(400, "no session identity — sign in or send X-Capcode-Session header")
    _check_rate_limit(owner, "evolve", max_calls=5, window_sec=60)

    # TIER — signed-in users carry a `tier` field on their user doc
    # (free/trial/paid). Anonymous sessions are always free-tier: trial and
    # paid both require an account so the 7-day clock and token ledger can
    # actually be tracked per-user.
    tier = "free"
    user_doc = None
    if owner.get("user_id"):
        user_doc = await db.users.find_one({"user_id": owner["user_id"]})
        tier = (user_doc or {}).get("tier", "free")
        if tier == "trial" and not (user_doc or {}).get("trial_started_at"):
            # First trial use starts the 7-day clock.
            await db.users.update_one(
                {"user_id": owner["user_id"]},
                {"$set": {"trial_started_at": datetime.now(timezone.utc).isoformat()}},
            )

    # BUDGET — check BEFORE doing any work or spending any tokens. Free tier
    # throttle is a soft message with an upgrade nudge, per spec; trial/paid
    # throttles are similar but reflect their own reason codes.
    budget = await _usage.check_budget(db, owner.get("user_id") or owner.get("anon"), tier)
    if not budget["ok"]:
        raise HTTPException(429, _usage.THROTTLE_MESSAGE)

    # resolve assignments from saved settings (or defaults) — only for roles
    # that are actually part of this tier's pipeline.
    role_sequence = _providers.role_sequence_for_tier(tier)
    assignments = None
    if req.session_id:
        s = await db.settings.find_one({"session_id": req.session_id}, {"_id": 0})
        if s:
            assignments = {r: s[r] for r in role_sequence if s.get(r)}

    # SEC — server-side consent gate for Venice/uncensored routing. Any role
    # resolved to provider == "venice" requires a *recent* consent record for
    # this exact owner (user_id OR anon session). We never trust the frontend
    # gate alone. On success we stamp the consent record's id/timestamp onto
    # the chain doc for a permanent audit trail.
    _resolved = assignments or {}
    _tier_defaults = _providers.assignments_for_tier(tier)
    _effective_roles = [(_resolved.get(r) or _tier_defaults.get(r)
                          or _providers.DEFAULT_ASSIGNMENTS.get(r) or {})
                        for r in role_sequence]
    _venice_used = any((a.get("provider") == "venice") for a in _effective_roles)
    _venice_consent_stamp: Optional[dict] = None
    if _venice_used:
        _consent_doc = await _get_recent_venice_consent(owner)
        if not _consent_doc:
            raise HTTPException(
                403,
                "venice/uncensored routing requires a fresh consent record — toggle "
                "uncensored on in Settings and accept the waiver before this build.",
            )
        _venice_consent_stamp = {
            "consent_id": _consent_doc.get("consent_id"),
            "accepted_at": _consent_doc.get("accepted_at"),
        }

    # gather top-verified prior chains as exemplars for the Teacher (brief)
    # AND for the Artist (real file structures) — this is the recursive loop.
    exemplar_docs = await db.chains.find(
        {"verified": True},
        {"_id": 0, "target_prompt": 1, "generations.name": 1,
         "generations.teacher_spec": 1, "generations.files": 1,
         "generations.stack": 1, "generations.composite_score": 1},
    ).sort("verified_at", -1).limit(5).to_list(5)
    exemplars = []
    exemplar_files = []
    for d in exemplar_docs:
        g = (d.get("generations") or [{}])[0]
        ts = g.get("teacher_spec") or {}
        exemplars.append({
            "target": d.get("target_prompt", ""),
            "name": g.get("name", ""),
            "artist_brief": ts.get("artist_brief", ""),
        })
        if g.get("files"):
            exemplar_files.append({
                "target": d.get("target_prompt", ""),
                "name": g.get("name", ""),
                "stack": g.get("stack", ""),
                "files": g.get("files", []),
                "score": g.get("composite_score", 0),
            })

    import uuid as _uuid
    chain_id = str(_uuid.uuid4())
    stub = _stamp_owner({
        "id": chain_id,
        "target_prompt": target,
        "depth": 1,
        "status": "running",
        "fallback_used": False,
        "verified": False,
        "generations": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }, owner)
    # Stamp the exact consent record onto the chain doc when uncensored/Venice
    # routing was used. Also back-fill the consent doc's `chain_id` so audit
    # walks both directions.
    if _venice_consent_stamp:
        stub["venice_consent"] = _venice_consent_stamp
        try:
            await db.venice_consent.update_one(
                {"consent_id": _venice_consent_stamp["consent_id"]},
                {"$set": {"chain_id": chain_id}},
            )
        except Exception as exc:  # audit best-effort; never block the build
            logger.warning("failed to back-fill consent.chain_id: %s", exc)
    await db.chains.insert_one({**stub})

    # CHECKPOINTED PIPELINE — /evolve only initializes state and returns
    # immediately. Nothing runs here. The frontend drives progress by
    # calling /advance repeatedly, once per stage, showing each stage's
    # report while the next call is already in flight. No single request
    # ever does more than one LLM call's worth of work, so there's no long-
    # running function for any serverless timeout to kill.
    #
    # `corrections` is the recursive Edit-Diffing loop (#7) closing itself —
    # real user corrections detected on PRIOR builds (across all users, same
    # cross-tenant learning model as verified exemplars) get fed into this
    # build's Teacher brief so it steers away from repeating them.
    corrections = await _edits.corrections_digest(db)
    eval_weights_doc = await _eval_meta.get_weights(db)
    segment = _prompt_evo.infer_segment(target)
    teacher_variant = await _prompt_evo.get_active_variant(db, "teacher", segment, chain_generator.SYSTEM_TEACHER)
    artist_variant = await _prompt_evo.get_active_variant(db, "artist", segment, chain_generator.SYSTEM_ARTIST)
    progress = chain_generator.new_progress(
        chain_id, target, tier,
        assignments=assignments, exemplars=exemplars, exemplar_files=exemplar_files,
        corrections=corrections, eval_weights=eval_weights_doc["weights"],
        teacher_prompt_template=teacher_variant["template"], teacher_variant_id=teacher_variant["id"],
        artist_prompt_template=artist_variant["template"], artist_variant_id=artist_variant["id"],
        segment=segment,
    )
    # SEC: never persist decrypted BYOK keys to the DB. Store only the
    # session_id so /advance can re-derive keys fresh on each stage call.
    progress["_session_id"] = req.session_id
    await db.chain_progress.insert_one(_stamp_owner(progress, owner))

    stub_out = {**stub}
    stub_out["created_at"] = datetime.fromisoformat(stub_out["created_at"])
    stub_out["next_stage"] = progress["stage_sequence"][0]
    return stub_out


@api.post("/chains/{chain_id}/advance")
async def advance_chain(chain_id: str, request: Request):
    """Runs exactly ONE stage of the pipeline and returns a human-readable
    report of what just happened. Call this repeatedly (frontend can fire
    the next call immediately after rendering the report) until done=true."""
    owner = await _owner_from_request(request)
    _check_rate_limit(owner, "advance", max_calls=40, window_sec=60)
    q = {"id": chain_id, **_owner_filter(owner)}
    progress = await db.chain_progress.find_one(q)
    if not progress:
        raise HTTPException(404, "chain not found or already finalized")

    # Re-derive BYOK keys fresh each call — never stored in chain_progress.
    progress["_user_keys"] = await _load_user_keys(progress.get("_session_id"))

    try:
        progress = await chain_generator.run_stage(progress)
    except chain_generator.ProviderRateLimitedError:
        # All keys in the tier's pool are rate-limited right now. Do NOT
        # fail the chain or delete its progress — leave it exactly where it
        # is so the same stage can just be retried once capacity frees up.
        raise HTTPException(429, _usage.THROTTLE_MESSAGE)
    except chain_generator.ArtistFailedError as exc:
        await db.chains.update_one(
            {"id": chain_id},
            {"$set": {"status": "failed", "error": str(exc),
                      "completed_at": datetime.now(timezone.utc).isoformat()}},
        )
        await db.chain_progress.delete_one({"id": chain_id})
        raise HTTPException(422, f"build failed: {exc}")
    except chain_generator.TeacherFailedError as exc:
        await db.chains.update_one(
            {"id": chain_id},
            {"$set": {"status": "failed", "error": str(exc),
                      "completed_at": datetime.now(timezone.utc).isoformat()}},
        )
        await db.chain_progress.delete_one({"id": chain_id})
        raise HTTPException(422, f"build failed: {exc}")
    except Exception as exc:
        logger.exception("chain %s stage failed: %s", chain_id, exc)
        await db.chains.update_one(
            {"id": chain_id},
            {"$set": {"status": "failed", "error": str(exc),
                      "completed_at": datetime.now(timezone.utc).isoformat()}},
        )
        await db.chain_progress.delete_one({"id": chain_id})
        raise HTTPException(500, f"stage failed: {exc}")

    report = progress.pop("_report")
    done = progress.pop("_done")
    stage_just_ran = progress.pop("_stage_ran")

    if done:
        product = progress["product"]
        completed_at = datetime.now(timezone.utc).isoformat()
        # Edit-Diffing loop (#7) starts here: stamp a sha256 manifest of the
        # final files so a future GitHub push/upload can cheaply tell which
        # files the user actually changed after delivery.
        build_manifest = _edits.sha256_manifest(product.get("files", []))
        await db.chains.update_one(
            {"id": chain_id},
            {"$push": {"generations": product},
             "$set": {"status": "complete", "fallback_used": progress["fallback_used"],
                      "completed_at": completed_at,
                      "build_manifest": build_manifest, "built_at": completed_at,
                      "teacher_variant_id": product.get("teacher_variant_id"),
                      "artist_variant_id": product.get("artist_variant_id")}},
        )
        km = (product.get("refinement") or {}).get("knowledge_module")
        if km:
            await db.knowledge_modules.insert_one({
                "id": str(uuid.uuid4()),
                "chain_id": chain_id,
                "tag": km["tag"],
                "source_path": km["source_path"],
                "code": km["code"],
                "target_prompt": km["target_prompt"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        await _usage.log_usage(
            db, owner.get("user_id") or owner.get("anon"), progress["tier"],
            provider="mixed", role="chain", tokens=progress["tokens_used"], chain_id=chain_id,
        )
        billing_result = None
        if owner.get("user_id") and product.get("refinement"):
            billing_result = await _billing.apply_chain_billing(
                db, owner["user_id"], chain_id, product["refinement"],
            )
        if progress.get("segment"):
            await _prompt_evo.maybe_evolve(db, "teacher", progress["segment"])
            await _prompt_evo.maybe_evolve(db, "artist", progress["segment"])
        await db.chain_progress.delete_one({"id": chain_id})
        return {"stage": stage_just_ran, "report": report, "done": True,
                "composite_score": product.get("composite_score"),
                "composite_gated": product.get("composite_gated", False),
                "refinement": product.get("refinement"),
                "billing": billing_result}

    progress.pop("_user_keys", None)
    await db.chain_progress.update_one({"id": chain_id}, {"$set": progress})
    next_stage = progress["stage_sequence"][progress["stage_index"]]
    return {"stage": stage_just_ran, "report": report, "done": False, "next_stage": next_stage}


@api.get("/chains", response_model=List[Chain])
async def list_chains(request: Request, limit: int = 30):
    owner = await _owner_from_request(request)
    query = _owner_filter(owner)
    # exclude heavy file payloads in list view
    docs = (
        await db.chains.find(
            query,
            {
                "_id": 0,
                "generations.files": 0,
            },
        )
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    for d in docs:
        if isinstance(d.get("created_at"), str):
            d["created_at"] = datetime.fromisoformat(d["created_at"])
        if isinstance(d.get("completed_at"), str):
            d["completed_at"] = datetime.fromisoformat(d["completed_at"])
        for g in d.get("generations", []):
            g.setdefault("files", [])
    return docs


@api.get("/chains/{chain_id}", response_model=Chain)
async def get_chain(chain_id: str, request: Request):
    owner = await _owner_from_request(request)
    q = {"id": chain_id, **_owner_filter(owner)}
    doc = await db.chains.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(404, "chain not found")
    for field in ("created_at", "completed_at"):
        if isinstance(doc.get(field), str):
            doc[field] = datetime.fromisoformat(doc[field])
    return doc


@api.get("/chains/{chain_id}/download")
async def download_chain(chain_id: str, request: Request):
    owner = await _owner_from_request(request)
    q = {"id": chain_id, **_owner_filter(owner)}
    doc = await db.chains.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(404, "chain not found")
    if doc.get("status") != "complete":
        raise HTTPException(409, f"chain status is {doc.get('status')} — not ready to download")
    buf, fname = _build_zip(doc)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@api.get("/chains/{chain_id}/download/{gen}")
async def download_gen(chain_id: str, gen: int, request: Request):
    owner = await _owner_from_request(request)
    q = {"id": chain_id, **_owner_filter(owner)}
    doc = await db.chains.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(404, "chain not found")
    buf, fname = _build_zip(doc, single_gen=gen)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@api.post("/chains/{chain_id}/workspace/{gen}")
async def open_in_vscode(chain_id: str, gen: int, request: Request):
    """Materialize a generation's files to /workspaces so the local code-server
    (Emergent's built-in VSCode-in-browser) can open the folder."""
    owner = await _owner_from_request(request)
    q = {"id": chain_id, **_owner_filter(owner)}
    doc = await db.chains.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(404, "chain not found")
    gens = doc.get("generations", [])
    g = next((x for x in gens if x.get("gen") == gen), None)
    if not g:
        raise HTTPException(404, f"gen {gen} not in chain")

    import re as _re
    import pathlib

    slug = _re.sub(r"[^A-Za-z0-9]+", "-", g.get("name") or f"gen-{gen}").strip("-") or f"gen-{gen}"
    workspace_root = pathlib.Path(os.environ.get("WORKSPACE_ROOT", "/tmp/capcode_workspaces"))
    workspace_dir = workspace_root / chain_id[:8] / slug
    workspace_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for f in g.get("files", []):
        target = workspace_dir / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["content"])
        # make .sh executable
        if target.suffix == ".sh":
            target.chmod(0o755)
        written.append(str(target.relative_to(workspace_dir)))

    return {
        "workspace_path": str(workspace_dir),
        "folder_query": f"?folder={workspace_dir}",
        "gen": gen,
        "name": g.get("name"),
        "files": written,
        # Users can open this folder in code-server (Emergent's built-in VSCode).
        # The exact code-server URL depends on the environment ingress; standard
        # Emergent layouts expose it via a port-1111 subdomain or /vscode path.
        "hint": "Open Emergent's code-server and use File > Open Folder → workspace_path, "
                "or append folder_query to the code-server URL.",
    }


@api.post("/chains/{chain_id}/verify")
async def verify_chain(chain_id: str, request: Request):
    """Human marks a completed chain as 'works'. Boosts its composite_score so
    it is more likely to be shown as a Teacher exemplar for future builds."""
    owner = await _owner_from_request(request)
    q = {"id": chain_id, **_owner_filter(owner)}
    doc = await db.chains.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(404, "chain not found")
    if doc.get("status") != "complete":
        raise HTTPException(409, "chain not complete — cannot verify")
    # boost each generation's composite_score by +0.5 (capped at 4.0)
    new_gens = list(doc.get("generations") or [])
    for g in new_gens:
        cur = float(g.get("composite_score") or 0)
        g["composite_score"] = round(min(4.0, cur + 0.5), 3)
    await db.chains.update_one(
        {"id": chain_id},
        {"$set": {
            "verified": True,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "generations": new_gens,
        }},
    )
    await _eval_meta.maybe_recompute(db)
    return {"id": chain_id, "verified": True}


@api.get("/chains/{chain_id}/stream")
async def stream_chain(chain_id: str, request: Request):
    """Server-Sent Events feed for a running chain. Emits Teacher/Artist token
    deltas and stage transitions. Closes when the chain completes."""
    owner = await _owner_from_request(request)
    q = {"id": chain_id, **_owner_filter(owner)}
    ok = await db.chains.find_one(q, {"_id": 0, "id": 1})
    if not ok:
        raise HTTPException(404, "chain not found")
    import asyncio as _asyncio
    q_stream = chain_generator.get_stream_queue(chain_id)

    async def gen():
        # initial ping so proxies flush headers
        yield "event: open\ndata: connected\n\n"
        while True:
            try:
                item = await _asyncio.wait_for(q_stream.get(), timeout=90)
            except _asyncio.TimeoutError:
                # keep-alive
                yield ": ping\n\n"
                # If the chain has already finished, exit.
                doc = await db.chains.find_one({"id": chain_id}, {"_id": 0, "status": 1})
                if doc and doc.get("status") in ("complete", "failed"):
                    break
                continue
            event = item.get("event", "message")
            if event == "done":
                yield "event: done\ndata: end\n\n"
                break
            data = item.get("data", "")
            # Escape newlines per SSE spec (each newline needs its own data: prefix).
            if isinstance(data, str) and "\n" in data:
                for line in data.split("\n"):
                    yield f"event: {event}\ndata: {line}\n\n"
            else:
                payload = data if isinstance(data, str) else json.dumps(data)
                yield f"event: {event}\ndata: {payload}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


class GithubPushRequest(BaseModel):
    repo: str  # target repo name (without owner)
    private: bool = True


@api.post("/chains/{chain_id}/push")
async def push_to_github(chain_id: str, req: GithubPushRequest, request: Request):
    """Push a completed chain's Product folder to the user's GitHub as a new repo.
    Requires the user to have saved a PAT + username under /api/settings."""
    owner = await _owner_from_request(request)
    q = {"id": chain_id, **_owner_filter(owner)}
    doc = await db.chains.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(404, "chain not found")
    if doc.get("status") != "complete":
        raise HTTPException(409, "chain not complete — cannot push")

    # SEC fix: the GitHub PAT must be looked up under the CALLER's own
    # verified identity (same owner used to fetch the chain above) — never
    # trust a client-supplied session_id here, or any caller could push using
    # someone else's stored token just by naming their session id in the body.
    settings_key = owner.get("anon")
    if not settings_key:
        raise HTTPException(400, "no session identity — send X-Capcode-Session header")
    settings = await db.settings.find_one({"session_id": settings_key}, {"_id": 0}) or {}
    gh = (settings.get("github") or {})
    token = (gh.get("token") or "").strip()
    username = (gh.get("username") or "").strip()
    if not token or not username:
        raise HTTPException(400, "github credentials missing — save your username + PAT in settings first")

    repo_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", req.repo).strip("-.") or f"capcode-{chain_id[:8]}"

    # 1) Create the repo on GitHub. Distinguish freshly-created (201) from
    # already-exists (422) so we don't --force-push over an existing repo.
    import httpx as _httpx
    was_created = False
    async with _httpx.AsyncClient(timeout=20) as http:
        r = await http.post(
            "https://api.github.com/user/repos",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
            json={"name": repo_name, "private": bool(req.private), "auto_init": False},
        )
        if r.status_code == 201:
            was_created = True
        elif r.status_code != 422:  # 422 = already exists
            raise HTTPException(502, f"github repo create failed: {r.status_code} {r.text[:200]}")

    # 2) Write files to a temp dir and push with git subprocess.
    gen = (doc.get("generations") or [{}])[0]
    files = gen.get("files") or []
    if not files:
        raise HTTPException(409, "chain has no files to push")

    import tempfile
    import subprocess
    import stat as _stat

    with tempfile.TemporaryDirectory(prefix="capcode-push-") as tmp:
        for f in files:
            rel = (f.get("path") or "").strip().lstrip("/")
            if not _exec._is_safe_relpath(rel):
                continue
            p = Path(tmp) / rel
            try:
                p.resolve().relative_to(Path(tmp).resolve())
            except ValueError:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["content"])
        # Include the spec docs too so the repo is self-explanatory.
        Path(tmp, "TEACHER_SPEC.md").write_text(_teacher_md(doc, gen))
        Path(tmp, "ARTIST_NOTES.md").write_text(_artist_md(gen))

        # SEC-004: keep the PAT OUT of argv. Use a GIT_ASKPASS helper script
        # + `HTTPS_REMOTE=https://github.com/user/repo.git` with credentials
        # supplied only via env-fed askpass. Also drop `--force` unless we
        # just created the repo this call.
        askpass_path = Path(tmp) / ".git_askpass.sh"
        askpass_path.write_text(
            '#!/usr/bin/env bash\n'
            'case "$1" in\n'
            '  Username*) echo "$GIT_USER" ;;\n'
            '  Password*) echo "$GIT_TOKEN" ;;\n'
            'esac\n'
        )
        askpass_path.chmod(0o700)
        remote_url = f"https://github.com/{username}/{repo_name}.git"

        def _run(cmd, cwd, with_creds=False):
            env = dict(os.environ)
            # Never let the AI-inherited scrubbing bleed into git — but also
            # don't leak our provider keys unnecessarily; git doesn't need them.
            for k in ("OPENROUTER_API_KEY", "NVIDIA_API_KEY", "VENICE_API_KEY"):
                env.pop(k, None)
            if with_creds:
                env["GIT_ASKPASS"] = str(askpass_path)
                env["GIT_TERMINAL_PROMPT"] = "0"
                env["GIT_USER"] = username
                env["GIT_TOKEN"] = token
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                  timeout=60, env=env)

        push_cmd = ["git", "push", "-u", "origin", "main"]
        if was_created:
            push_cmd.append("--force")  # brand-new repo; safe to force initial state

        steps = [
            (["git", "init", "-b", "main"], False),
            (["git", "config", "user.email", "capcode@emergent.host"], False),
            (["git", "config", "user.name", "CapCode"], False),
            (["git", "add", "-A"], False),
            (["git", "commit", "-m", f"CapCode build: {doc.get('target_prompt','')[:120]}"], False),
            (["git", "remote", "add", "origin", remote_url], False),
            (push_cmd, True),
        ]
        for cmd, needs_creds in steps:
            res = _run(cmd, tmp, with_creds=needs_creds)
            if res.returncode != 0:
                err = (res.stderr or res.stdout or "").replace(token, "***")
                if cmd[:3] == ["git", "remote", "add"] and "already exists" in err:
                    continue
                raise HTTPException(502, f"git step {' '.join(cmd[:3])} failed: {err[:300]}")

        # Edit-Diffing loop (#7): remember exactly which commit we just
        # pushed so a later "check for edits" can diff HEAD against it via
        # GitHub's compare API — no local clone needed at check time.
        sha_res = _run(["git", "rev-parse", "HEAD"], tmp, with_creds=False)
        commit_sha = (sha_res.stdout or "").strip()

    if commit_sha:
        await db.chains.update_one(
            {"id": chain_id},
            {"$set": {"github": {
                "repo": f"{username}/{repo_name}",
                "commit_sha": commit_sha,
                "pushed_at": datetime.now(timezone.utc).isoformat(),
            }}},
        )

    return {
        "id": chain_id,
        "repo": f"{username}/{repo_name}",
        "url": f"https://github.com/{username}/{repo_name}",
        "private": bool(req.private),
        "created": was_created,
    }


@api.post("/chains/{chain_id}/check-edits")
async def check_edits(chain_id: str, request: Request, file: UploadFile = File(None)):
    """Edit-Diffing loop (#7): looks for real changes the user made to a
    delivered build. Two ways in — whichever applies:
      1. Chain was pushed to GitHub -> diff the pushed commit against the
         repo's current HEAD via GitHub's compare API (no upload needed).
      2. No push (or you want to check local-only edits) -> upload the
         edited project as a .zip and we hash+difflib it against what was
         originally generated.
    Detected hunks are stored permanently and feed every future build's
    Teacher prompt via corrections_digest()."""
    owner = await _owner_from_request(request)
    _check_rate_limit(owner, "check-edits", max_calls=10, window_sec=60)
    q = {"id": chain_id, **_owner_filter(owner)}
    doc = await db.chains.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(404, "chain not found")
    if doc.get("status") != "complete":
        raise HTTPException(409, "chain not complete")

    changed: list[dict] = []

    # Prefer an explicit upload when the caller sent one — pushing to GitHub
    # doesn't retire the upload path, and a stale/removed PAT shouldn't turn
    # a valid attached zip into a 400.
    if file is not None:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:
            raise HTTPException(413, "zip too large (10MB max)")
        raw = await file.read()
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(413, "zip too large (10MB max)")
        uploaded: dict[str, str] = {}
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    if not _exec._is_safe_relpath(name):
                        continue
                    try:
                        uploaded[name] = zf.read(name).decode("utf-8", "ignore")
                    except Exception:
                        continue
        except zipfile.BadZipFile:
            raise HTTPException(400, "not a valid zip file")
        changed = await _edits.check_upload_edits(db, doc, uploaded)
    elif doc.get("github"):
        settings_key = owner.get("anon")
        settings = await db.settings.find_one({"session_id": settings_key}, {"_id": 0}) or {} if settings_key else {}
        token = ((settings.get("github") or {}).get("token") or "").strip()
        if not token:
            raise HTTPException(400, "github token missing — save your PAT in settings to check pushed edits")
        changed = await _edits.check_git_edits(db, doc, token)
    else:
        raise HTTPException(400, "push this chain to GitHub first, or upload your edited files as a .zip")

    if changed:
        await _eval_meta.maybe_recompute(db)
    return {"chain_id": chain_id, "changed_files": len(changed), "diffs": changed}


@api.get("/chains/{chain_id}/edit-diffs")
async def get_edit_diffs(chain_id: str, request: Request):
    owner = await _owner_from_request(request)
    q = {"id": chain_id, **_owner_filter(owner)}
    doc = await db.chains.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(404, "chain not found")
    rows = await db.edit_diffs.find({"chain_id": chain_id}).sort("detected_at", -1).to_list(50)
    for r in rows:
        r.pop("_id", None)
    return {"chain_id": chain_id, "diffs": rows}


@api.get("/eval-weights")
async def get_eval_weights():
    """Public, read-only — Layer #5's current scoring rubric, for
    transparency (surfaced on generation cards in the UI)."""
    return await _eval_meta.get_weights(db)


@api.get("/prompt-variants")
async def get_prompt_variants():
    """Public, read-only — Layer #1's current Teacher/Artist prompt
    champions + any in-flight A/B challengers, for transparency."""
    out = {}
    for role in ("teacher", "artist"):
        champs = await db.prompt_variants.find({"role": role, "status": "champion"}).to_list(50)
        challengers = await db.prompt_variants.find({"role": role, "status": "challenger"}).to_list(50)
        out[role] = {
            "champions": [{"segment": c["segment"], "version": c["version"], "id": c["id"]} for c in champs],
            "challengers": [{"segment": c["segment"], "version": c["version"], "id": c["id"],
                              "challenging_variant_id": c.get("challenging_variant_id")} for c in challengers],
        }
    return out


app.include_router(api)
_tiers.init(db, _owner_from_request)
app.include_router(_tiers.router)
# CORS: cookies require an explicit origin (browsers reject wildcard + credentials).
# SEC fix: the old fallback matched ANY origin (`https?://.*`) with credentials
# enabled whenever CORS_ORIGINS was unset — effectively CORS disabled for
# every deployment that forgot to set it. Fallback is now scoped to this
# platform's own preview domains + localhost dev only; any real deployment
# MUST set CORS_ORIGINS explicitly or cross-origin requests are rejected.
_cors_env = (os.environ.get("CORS_ORIGINS") or "").strip()
if _cors_env and _cors_env != "*":
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origins=[o.strip() for o in _cors_env.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?|https://[a-zA-Z0-9.-]+\.(emergentagent\.com|emergent\.host)",
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.on_event("startup")
async def _connect_docdb():
    """MUST run before any other startup handler that touches `db`."""
    await docdb.connect(POSTGRES_URL)


@app.on_event("startup")
async def _validate_default_rater():
    """Ping the default rater model with a tiny prompt so we catch bad slugs
    (like the previous `inclusionai/ling-3.0-flash:free` -> 404) at boot, not
    on the first user build."""
    try:
        r = _providers.DEFAULT_ASSIGNMENTS["rater"]
        client = _providers.openai_client_for(r["provider"])
        if client is None:
            logger.warning("rater validation skipped: no key for provider %s", r["provider"])
            return
        try:
            resp = await client.chat.completions.create(
                model=r["model"],
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=2, temperature=0,
            )
            if not resp.choices:
                raise RuntimeError("no choices in response")
            logger.info("rater validation ok: %s/%s", r["provider"], r["model"])
        except Exception as exc:
            logger.error("rater validation FAILED: %s/%s -> %s (falling back at runtime)",
                         r["provider"], r["model"], exc)
    except Exception as exc:
        logger.warning("rater validation raised: %s", exc)


@app.on_event("startup")
async def _sweep_orphans():
    """On boot, mark any chains that were still 'running' as failed. A prior
    process (supervisor reload, hot-reload, crash) cannot resume its background
    task, so leaving them 'running' would deadlock the UI polling forever."""
    try:
        r = await db.chains.update_many(
            {"status": "running"},
            {"$set": {
                "status": "failed",
                "error": "backend restarted before build finished — try again",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        if r.modified_count:
            logger.info("startup sweep: marked %d orphaned chain(s) as failed", r.modified_count)
    except Exception as exc:
        logger.warning("startup sweep failed: %s", exc)


@app.on_event("startup")
async def _seed_prompt_variants():
    """Layer #1 — seed the global champion prompts (Teacher + Artist) once.
    Idempotent; never clobbers a promoted challenger on hot-reload/restart."""
    try:
        await _prompt_evo.ensure_seed(db, "teacher", _prompt_evo.SEED_TEACHER_TEMPLATE)
        await _prompt_evo.ensure_seed(db, "artist", _prompt_evo.SEED_ARTIST_TEMPLATE)
    except Exception as exc:
        logger.warning("prompt_evolution seeding failed: %s", exc)


@app.on_event("shutdown")
async def shutdown_db():
    await docdb.close()
