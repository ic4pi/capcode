"""
CapCode — recursive bot-builder pipeline.

STRICT topology (as requested by the user):
    Human target prompt
        -> TEACHER bot   (rigid, strict, uses top-verified prior chains as exemplars)
        -> ARTIST  bot   (creative, novel product design following the Teacher's brief)
        -> PRODUCT       (rendered downloadable code folder + one correction pass on failure)
        -> RATER         (5-dim scorecard)

No 6-role dev-team. No plan/architect/builder split. One human input, three bots.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import seed_template
import executor as _exec
import providers as _providers
import coverage as _coverage
import usage as _usage

logger = logging.getLogger("chain")


class ProviderRateLimitedError(Exception):
    """Raised when a provider call is rate-limited (HTTP 429) and there's no
    fallback to try (free/trial tiers on Groq intentionally have none — see
    _call_role's tier="free" fallback suppression). server.py's /advance
    catches this specifically and surfaces usage.THROTTLE_MESSAGE instead of
    a generic failure, and — importantly — does NOT mark the chain failed or
    delete its progress, so the same stage can just be retried later."""
    pass


def _is_rate_limited(exc: Exception) -> bool:
    # openai-python's RateLimitError (used for every provider here, since
    # Groq/NVIDIA/OpenRouter/Venice are all OpenAI-compatible endpoints).
    try:
        from openai import RateLimitError
        if isinstance(exc, RateLimitError):
            return True
    except ImportError:
        pass
    # Fallback duck-typing in case of a wrapped/different exception shape.
    return getattr(exc, "status_code", None) == 429

# Any provider having a key means we can evolve.
ENABLED = any(_providers.provider_available(p) for p in ("openrouter", "nvidia", "venice"))


# ---------------------------------------------------------------------------
# Per-chain stream broadcasters (SSE) — one asyncio.Queue per active chain
# ---------------------------------------------------------------------------
import asyncio
_stream_queues: dict[str, asyncio.Queue] = {}


def get_stream_queue(chain_id: str) -> asyncio.Queue:
    q = _stream_queues.get(chain_id)
    if q is None:
        q = asyncio.Queue()
        _stream_queues[chain_id] = q
    return q


def close_stream(chain_id: str):
    q = _stream_queues.pop(chain_id, None)
    if q is not None:
        try:
            q.put_nowait({"event": "done"})
        except Exception:
            pass


async def _emit(chain_id: Optional[str], event: str, data):
    if not chain_id:
        return
    q = _stream_queues.get(chain_id)
    if q is None:
        return
    try:
        q.put_nowait({"event": event, "data": data})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------
def _client_for(provider: str, user_keys: Optional[dict] = None):
    """Fresh client if user provided BYOK for this provider, else cached env client."""
    user_key = (user_keys or {}).get(provider) if user_keys else None
    if user_key:
        return _providers.openai_client_for(provider, api_key=user_key)
    if provider not in _client_cache:
        _client_cache[provider] = _providers.openai_client_for(provider)
    return _client_cache[provider]


_client_cache: dict[str, object] = {}


async def _chat(model: str, messages: list[dict], provider: str = "nvidia",
                fallback: Optional[tuple[str, str]] = None,
                user_keys: Optional[dict] = None,
                stream_chain_id: Optional[str] = None,
                stream_event: Optional[str] = None,
                **kwargs) -> Optional[str]:
    client = _client_for(provider, user_keys)
    if client is None:
        if fallback:
            return await _chat(fallback[1], messages, provider=fallback[0],
                               user_keys=user_keys,
                               stream_chain_id=stream_chain_id,
                               stream_event=stream_event, **kwargs)
        return None
    do_stream = bool(stream_chain_id and stream_event)
    try:
        if do_stream:
            # Streaming path — accumulate content, emit deltas over SSE.
            stream = await client.chat.completions.create(
                model=model, messages=messages, stream=True, **kwargs)
            buf = []
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None) or ""
                if piece:
                    buf.append(piece)
                    await _emit(stream_chain_id, stream_event, piece)
            out = "".join(buf).strip()
            return out
        r = await client.chat.completions.create(model=model, messages=messages, **kwargs)
        msg = r.choices[0].message
        out = (msg.content or "").strip()
        if not out:
            reasoning = getattr(msg, "reasoning_content", None) or ""
            paras = [p.strip() for p in reasoning.split("\n\n") if p.strip()]
            out = paras[-1] if paras else reasoning.strip()
        return out
    except Exception as exc:
        logger.error("%s/%s call failed: %s", provider, model, exc)
        if _is_rate_limited(exc):
            if fallback:
                return await _chat(fallback[1], messages, provider=fallback[0],
                                   user_keys=user_keys,
                                   stream_chain_id=stream_chain_id,
                                   stream_event=stream_event, **kwargs)
            raise ProviderRateLimitedError(f"{provider}/{model} rate-limited") from exc
        if fallback:
            return await _chat(fallback[1], messages, provider=fallback[0],
                               user_keys=user_keys,
                               stream_chain_id=stream_chain_id,
                               stream_event=stream_event, **kwargs)
        return None


def _repair_truncated_json(raw: str, start: int) -> str | None:
    """When a model hits max_tokens mid-JSON, close the open string/braces so
    the partial output still parses. Returns a repaired candidate or None."""
    depth = 0
    in_str = False
    esc = False
    last_complete_end = -1
    for i in range(start, len(raw)):
        c = raw[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{" or c == "[":
                depth += 1
            elif c == "}" or c == "]":
                depth -= 1
                if depth == 0:
                    last_complete_end = i + 1
    if last_complete_end > 0 and last_complete_end == len(raw):
        return None  # complete, no repair needed
    # Truncated. Close the open string (if any) then close remaining braces/brackets.
    # Track brace/bracket types to close them in reverse order.
    stack: list[str] = []
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        c = raw[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                stack.append("}")
            elif c == "[":
                stack.append("]")
            elif c == "}" or c == "]":
                if stack:
                    stack.pop()
    if not stack and not in_str:
        return None
    tail = raw[start:]
    # Drop a trailing partial token like `"conten` or `"content": "hello wo` — cut at
    # the last comma or opening-brace so we don't leave a dangling field.
    # Strategy: if we're inside a string, close it. Then remove any dangling
    # `"key":` or trailing comma at end.
    if in_str:
        # find the position of the opening quote of the unfinished string
        # so we can drop the whole partial field to keep JSON valid.
        depth2 = 0
        in_s2 = False
        esc2 = False
        last_boundary = -1  # position of last comma or `{` or `[` at depth root
        open_quote_pos = -1
        for j in range(len(tail)):
            c = tail[j]
            if in_s2:
                if esc2:
                    esc2 = False
                elif c == "\\":
                    esc2 = True
                elif c == '"':
                    in_s2 = False
            else:
                if c == '"':
                    in_s2 = True
                    if j > 0:
                        open_quote_pos = j
                elif c in ("{", "[", ","):
                    last_boundary = j
                elif c in ("}", "]"):
                    pass
        # Prefer cutting at the last boundary before the unclosed string.
        cut = last_boundary if 0 <= last_boundary < open_quote_pos else open_quote_pos
        if cut > 0:
            tail = tail[:cut]
    # strip trailing dangling `"key":` or `,` or whitespace
    tail = re.sub(r"[\s,]*\"?[A-Za-z_][A-Za-z0-9_]*\"?\s*:\s*$", "", tail)
    tail = re.sub(r"[\s,]+$", "", tail)
    # Recompute what still needs closing.
    stack = []
    in_str = False
    esc = False
    for c in tail:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                stack.append("}")
            elif c == "[":
                stack.append("]")
            elif c in ("}", "]") and stack:
                stack.pop()
    if in_str:
        tail += '"'
    tail += "".join(reversed(stack))
    return tail


def _extract_json(raw: str) -> dict:
    """Extract the first top-level JSON object from a model response.
    Tracks string state so braces inside string literals don't fool the parser.
    Also attempts repair on truncated (max_tokens hit) responses."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    # If wrapped in a ```json fenced block, extract that first.
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    candidates = []
    if m:
        candidates.append(m.group(1))
    start = raw.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        end = -1
        for i in range(start, len(raw)):
            c = raw[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
        if end > 0:
            candidates.append(raw[start:end])
        # ALSO try repairing truncated JSON
        repaired = _repair_truncated_json(raw, start)
        if repaired:
            candidates.append(repaired)
    for cand in candidates:
        try:
            return json.loads(cand, strict=False)
        except Exception:
            try:
                return json.loads(re.sub(r",\s*([}\]])", r"\1", cand), strict=False)
            except Exception:
                continue
    logger.warning("JSON parse failed. head: %s | tail: %s", raw[:200], raw[-200:])
    return {}


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------
SYSTEM_TEACHER = """You are the TEACHER — Bot 1 in CapCode's two-bot chain. Rigid, strict, provider-educated. Given the HUMAN TARGET (the app they want built), you design a spec for the ARTIST BOT that will actually construct it. You do NOT design the product yourself — you design the second bot's brief.

Detect the correct stack from the target (python/fastapi, node/vite/react, rust/cargo, go, webgl/html). Default to python-fastapi if unclear.

You are shown TOP-VERIFIED PRIOR CHAINS as exemplars (human-marked as "works"). Learn from their patterns.

Output STRICT JSON only:
{
  "name": "TeacherSpec-<short-name>",
  "target": "<echo of the human's target>",
  "stack": "python-fastapi | node-vite | rust | go | webgl",
  "artist_brief": "3-5 sentences briefing the Artist on what to build",
  "must_have": ["3-5 concrete requirements the product MUST satisfy"],
  "should_avoid": ["2-3 anti-patterns"],
  "accent_hex": "#rrggbb",
  "accent2_hex": "#rrggbb"
}
"""

SYSTEM_ARTIST = """You are the ARTIST — Bot 2 in CapCode's two-bot chain. Creative, sharp, novel. Given the Teacher's spec you WRITE THE ACTUAL SOURCE CODE for the app the human asked for. Do not describe. Do not hand-wave. Write real, runnable files.

Every string in `files[].content` MUST be complete source code — no "..." placeholders, no "TODO" stubs, no imports of packages you don't declare, no fake API keys. The app must start with a single `bash run.sh` and satisfy every must_have in the Teacher spec.

Stack-specific rules:
  - python-fastapi : include backend/server.py (FastAPI app with a real /api route that DOES the thing), backend/requirements.txt, run.sh (uvicorn on $APP_PORT). Optional frontend/index.html.
  - node-vite      : include src/main.jsx (real React with real state/fetch/UI for the request), index.html, package.json (with correct deps), vite.config.js, run.sh (npm install + vite --host --port $APP_PORT). NO server-side rendering.
  - rust           : include Cargo.toml, src/main.rs (real logic), run.sh (cargo run).
  - go             : include go.mod, main.go (real logic), run.sh (go run main.go).
  - webgl          : include a single self-contained index.html with real inline JS doing the requested thing, run.sh (python3 -m http.server $APP_PORT).

For anything that needs a public API (prices, weather, quotes, jokes, etc), pick a well-known keyless public endpoint (CoinGecko, wttr.in, jsonplaceholder, etc). Never invent endpoints.

Output STRICT JSON only — no prose before or after:
{
  "name": "PascalCase product name",
  "tagline": "one line",
  "philosophy": "2 sentences — your creative angle",
  "improvement_note": "how you satisfied the Teacher's must_have while adding novelty",
  "stack": "<echo the teacher's stack>",
  "accent_hex": "#rrggbb",
  "accent2_hex": "#rrggbb",
  "files": [
    {"path": "relative/path.ext", "content": "FULL FILE CONTENT — no ellipsis"},
    ...
  ],
  "weights": {"helpfulness":0.35,"correctness":0.30,"coherence":0.20,"complexity":0.10,"verbosity":-0.05}
}
"""

SYSTEM_ARCHITECT = """You are the ARCHITECT — runs BEFORE the Artist in CapCode's extended (trial/paid) pipeline. Given the Teacher's spec, add concrete structural guidance the Artist should follow: file/module layout, how components should talk to each other, and one or two specific implementation choices that would make this build more novel or interesting than the obvious default — WITHOUT sacrificing that it must actually run. Output ONLY a JSON object: {"architecture_notes": "<2-4 sentences of concrete guidance>"}"""

SYSTEM_REVIEWER = """You are the REVIEWER — runs AFTER the first execution attempt in CapCode's extended pipeline, before the Corrector. Given the product summary and execution result, decide whether this needs a correction pass even if it technically started (e.g. an obviously broken UI, a route that's a stub, missing the core feature asked for). Output ONLY JSON: {"needs_fix": true|false, "review_notes": "<1-3 sentences, specific>"}"""

SYSTEM_CORRECTOR = """You are the CORRECTOR. The generated app FAILED TO START, or the Reviewer flagged it as incomplete — given the source files, the real stderr (if any), and any reviewer notes, output the CORRECTED JSON (same schema as the Artist, including a `files` array). Focus on the smallest edits that make it start and address the reviewer's concerns. Rewrite only the files that need to change; you may include unchanged files verbatim."""

SYSTEM_RATER = (
    "You are a strict code-quality rater. Given a short brief about a build, "
    "score it on FIVE dimensions, each on a 0.0-4.0 scale where 4.0 is excellent "
    "and 0.0 is broken. Base your score on the brief, not on any example. "
    "Dimensions:\n"
    "  helpfulness — does it plausibly do what the target asked?\n"
    "  correctness — did the app start and run?\n"
    "  coherence  — is the design consistent?\n"
    "  complexity — depth/ambition (higher = more ambitious).\n"
    "  verbosity  — length appropriateness (2.0 = right-sized).\n"
    "Output ONLY a JSON object with these five numeric keys, e.g. "
    '{"helpfulness":2.7,"correctness":3.2,"coherence":2.5,"complexity":1.9,"verbosity":2.1}. '
    "Pick real, differentiated numbers — do not repeat the example."
)


# ---------------------------------------------------------------------------
# Bot steps
# ---------------------------------------------------------------------------
def _estimate_tokens(*texts: str) -> int:
    """Rough token estimate (chars/4) for usage-budget purposes. Not billing-
    grade — real provider usage varies by tokenizer — but good enough to
    gate free/trial pools without threading provider-specific usage objects
    through the streaming and non-streaming _chat paths."""
    total_chars = sum(len(t or "") for t in texts)
    return max(1, total_chars // 4)


async def _call_role(role: str, messages: list[dict], assignment: Optional[dict],
                     user_keys: Optional[dict] = None,
                     stream_chain_id: Optional[str] = None,
                     stream_event: Optional[str] = None,
                     tier: Optional[str] = None,
                     **kwargs) -> tuple[Optional[str], int]:
    """Returns (text, estimated_tokens). estimated_tokens covers input+output
    for this single call and is meant to be summed by the caller into a
    per-chain running total for usage.log_usage().

    tier="free" suppresses the NVIDIA/Venice/OpenRouter fallback entirely —
    free tier is Groq-only by spec; if the Groq call fails (rate limit, key
    pool exhausted mid-call, etc.) it should surface as a failure the caller
    can turn into the throttle message, not silently spend paid credits on a
    free user by falling through to another provider.
    """
    a = assignment or _providers.DEFAULT_ASSIGNMENTS[role]
    if tier == "free":
        fb = None
    elif tier == "trial":
        # Trial is NVIDIA-only end to end — never fall through to a metered
        # OpenRouter/Venice model even on an NVIDIA error/rate-limit.
        fb = _providers.TRIAL_FALLBACKS.get(role)
    else:
        fbd = _providers.DEFAULT_ASSIGNMENTS.get(f"{role}_fallback", {"provider": "nvidia", "model": "z-ai/glm-5.1"})
        fb = (fbd["provider"], fbd["model"])
    out = await _chat(a["model"], messages, provider=a["provider"],
                       fallback=fb,
                       user_keys=user_keys,
                       stream_chain_id=stream_chain_id,
                       stream_event=stream_event, **kwargs)
    input_text = "\n".join(m.get("content", "") for m in messages if isinstance(m, dict))
    tokens = _estimate_tokens(input_text, out or "")
    return out, tokens


class TeacherFailedError(Exception):
    """Raised when the Teacher gets no response at all from its provider
    (missing/invalid key, provider outage) — must fail loudly instead of
    silently substituting a placeholder spec that masks the real problem."""


async def teacher_step(target: str, exemplars: list[dict], assignment: Optional[dict] = None,
                       user_keys: Optional[dict] = None, chain_id: Optional[str] = None,
                       tier: Optional[str] = None, corrections: str = "",
                       prompt_template: Optional[str] = None) -> dict:
    exemplar_block = "\n\n".join(
        f"[verified — target: {e.get('target','?')} — product: {e.get('name','?')}]\n"
        f"artist_brief was: {e.get('artist_brief','?')}"
        for e in exemplars[:3]
    ) or "(no verified exemplars yet — apply first principles)"
    corrections_block = (
        f"\n\nCOMMON USER CORRECTIONS ACROSS PRIOR BUILDS (steer the brief to avoid "
        f"repeating these — real edits users had to make after a build was delivered):\n{corrections}"
        if corrections else ""
    )
    await _emit(chain_id, "stage", "teacher")
    user_data = (f"HUMAN TARGET:\n{target}\n\nTOP-VERIFIED PRIOR CHAINS:\n{exemplar_block}"
                f"{corrections_block}\n\nDesign the Teacher spec as strict JSON.")
    # Layer #1 — Prompt Self-Tuning: when server.py resolved an active
    # champion/challenger variant, its full [ROLE]/[CONSTRAINTS]/[NEGATIONS]/
    # [EXAMPLES]/[DATA] template REPLACES the hardcoded SYSTEM_TEACHER + user
    # message split entirely — {{USER_INPUT}} is substituted with the same
    # dynamic content that would have gone into the user message, and the
    # whole thing is sent as a single message, per the template's own shape.
    if prompt_template:
        messages = [{"role": "user", "content": prompt_template.replace("{{USER_INPUT}}", user_data)}]
    else:
        messages = [{"role": "system", "content": SYSTEM_TEACHER}, {"role": "user", "content": user_data}]
    raw, tokens = await _call_role(
        "teacher", messages,
        assignment, user_keys=user_keys, tier=tier,
        stream_chain_id=chain_id, stream_event="teacher_delta",
        temperature=0.4, max_tokens=800,
    )
    if not (raw or "").strip():
        raise TeacherFailedError(
            "Teacher got no response from its model — the provider may be "
            "unavailable or missing an API key. Try a different Teacher model "
            "in Settings, or try again shortly."
        )
    parsed = _extract_json(raw or "") or {}
    parsed.setdefault("target", target)
    parsed.setdefault("name", f"TeacherSpec-{target[:20]}")
    parsed.setdefault("stack", "python-fastapi")
    parsed.setdefault("artist_brief", f"Build: {target}. Keep it minimal and runnable.")
    parsed.setdefault("must_have", ["compiles/starts", "single-command run", "clear README"])
    parsed.setdefault("should_avoid", ["over-engineering"])
    parsed.setdefault("accent_hex", "#7cffb2")
    parsed.setdefault("accent2_hex", "#ff79c6")
    parsed["_tokens"] = tokens
    return parsed


async def architect_step(target: str, teacher_spec: dict, assignment: Optional[dict] = None,
                         user_keys: Optional[dict] = None, chain_id: Optional[str] = None,
                         tier: Optional[str] = None) -> dict:
    """Trial/paid-tier only. Runs between Teacher and Artist — adds structural
    guidance and nudges the Artist toward a less obvious (but still working)
    approach. Skipped entirely on the free tier's 2-role pipeline."""
    await _emit(chain_id, "stage", "architect")
    raw, tokens = await _call_role(
        "architect",
        [{"role": "system", "content": SYSTEM_ARCHITECT},
         {"role": "user", "content":
             f"HUMAN TARGET:\n{target}\n\nTEACHER SPEC:\n{json.dumps(teacher_spec, indent=2)}"}],
        assignment, user_keys=user_keys, tier=tier,
        stream_chain_id=chain_id, stream_event="architect_delta",
        temperature=0.6, max_tokens=400,
    )
    parsed = _extract_json(raw or "") or {}
    parsed.setdefault("architecture_notes", "")
    parsed["_tokens"] = tokens
    return parsed


async def reviewer_step(product: dict, exec_result: dict, assignment: Optional[dict] = None,
                        user_keys: Optional[dict] = None, chain_id: Optional[str] = None,
                        tier: Optional[str] = None) -> dict:
    """Trial/paid-tier only. Runs after the first execution attempt, before
    the Corrector — can flag a correction pass even on a build that
    technically started but doesn't actually satisfy the target."""
    await _emit(chain_id, "stage", "reviewer")
    brief = json.dumps({
        "name": product.get("name"), "target": product.get("target_prompt"),
        "exec_started": exec_result.get("started"),
        "exec_error_tail": (exec_result.get("stderr") or "")[-300:],
        "file_paths": [f.get("path") for f in (product.get("files") or [])[:20]],
    }, indent=2)
    raw, tokens = await _call_role(
        "reviewer",
        [{"role": "system", "content": SYSTEM_REVIEWER},
         {"role": "user", "content": brief}],
        assignment, user_keys=user_keys, tier=tier, temperature=0.2, max_tokens=400,
    )
    parsed = _extract_json(raw or "") or {}
    parsed.setdefault("needs_fix", not bool(exec_result.get("started")))
    parsed.setdefault("review_notes", "")
    parsed["_tokens"] = tokens
    return parsed


# ---------------------------------------------------------------------------
# Checkpointed pipeline — one stage per HTTP call, no long-running function.
# Each call to run_stage() executes exactly ONE stage, persists nowhere
# itself (server.py owns writing `progress` back to db.chain_progress
# between calls), and returns a human-readable report the frontend can show
# immediately while the NEXT stage's call is already in flight. Solves the
# serverless-timeout problem architecturally — no single request ever runs
# more than one LLM call's worth of work.
# ---------------------------------------------------------------------------
STAGE_SEQUENCE = {
    "free":  ["teacher", "artist", "execute", "correct", "rate", "score"],
    "trial": ["teacher", "architect", "artist", "execute", "reviewer", "correct", "rate", "score"],
    "paid":  ["teacher", "architect", "artist", "execute", "reviewer", "correct", "rate", "score"],
}


def new_progress(chain_id: str, target_prompt: str, tier: str,
                 assignments: Optional[dict] = None,
                 exemplars: Optional[list[dict]] = None,
                 exemplar_files: Optional[list[dict]] = None,
                 corrections: str = "",
                 eval_weights: Optional[dict] = None,
                 teacher_prompt_template: Optional[str] = None,
                 teacher_variant_id: Optional[str] = None,
                 artist_prompt_template: Optional[str] = None,
                 artist_variant_id: Optional[str] = None,
                 segment: Optional[str] = None) -> dict:
    """Initial checkpoint state for a fresh chain. server.py writes this to
    db.chain_progress right after budget-checking, BEFORE any stage runs."""
    role_sequence = _providers.role_sequence_for_tier(tier)
    a = dict(_providers.assignments_for_tier(tier))
    for role in role_sequence:
        if assignments and assignments.get(role):
            a[role] = assignments[role]
    return {
        "id": chain_id,
        "target_prompt": target_prompt,
        "tier": tier,
        "role_sequence": role_sequence,
        "stage_sequence": STAGE_SEQUENCE.get(tier, STAGE_SEQUENCE["free"]),
        "stage_index": 0,
        "assignments": a,
        "exemplars": exemplars or [],
        "exemplar_files": exemplar_files or [],
        "corrections": corrections or "",
        "eval_weights": eval_weights,
        "teacher_prompt_template": teacher_prompt_template,
        "teacher_variant_id": teacher_variant_id,
        "artist_prompt_template": artist_prompt_template,
        "artist_variant_id": artist_variant_id,
        "segment": segment,
        "tokens_used": 0,
        "fallback_used": False,
        "needs_fix": False,
        "review_notes": "",
        # accumulated pipeline outputs, filled in as stages run:
        "teacher_spec": None,
        "artist_spec": None,
        "product": None,
        "exec_result": None,
        "scores": None,
    }


def _report(stage: str, progress: dict) -> str:
    """Deterministic, no-extra-LLM-call human-readable summary of what a
    stage just did — shown to the user immediately, while the next stage's
    call is already starting in the background."""
    if stage == "teacher":
        ts = progress["teacher_spec"] or {}
        return (f"Teacher reviewed your request and designed a build spec — "
                f"targeting a {ts.get('stack', 'app')} build named "
                f"\u201c{ts.get('name', 'this project')}\u201d. "
                f"Handing the brief to the {'Architect' if 'architect' in progress['role_sequence'] else 'Artist'} next.")
    if stage == "architect":
        notes = (progress.get("_architect_notes") or "").strip()
        return (f"Architect added structural guidance"
                + (f": {notes}" if notes else ".")
                + " Passing the refined brief to the Artist to write the actual code.")
    if stage == "artist":
        n_files = len((progress["artist_spec"] or {}).get("files") or [])
        return f"Artist wrote {n_files} file(s). Moving on to run it and see if it actually works."
    if stage == "execute":
        er = progress["exec_result"] or {}
        ok = er.get("started")
        return (f"Build {'started successfully' if ok else 'failed to start'} "
                f"(exit code {er.get('exit_code')}). "
                + ("Sending it to Reviewer for a second look." if "reviewer" in progress["role_sequence"]
                   else ("Needs a correction pass." if not ok else "Looking clean — on to scoring.")))
    if stage == "reviewer":
        flagged = progress.get("needs_fix")
        return (f"Reviewer {'flagged issues' if flagged else 'signed off — no issues found'}"
                + (f": {progress.get('review_notes', '')}" if flagged and progress.get("review_notes") else ".")
                + (" Sending to Corrector." if flagged else " Skipping correction, moving to scoring."))
    if stage == "correct":
        if progress.get("_correction_ran"):
            return "Corrector patched the build and re-ran it to confirm the fix took."
        return "No correction needed — build was already working."
    if stage == "rate":
        return "Rater scored the build against your original request."
    if stage == "score":
        cs = (progress["product"] or {}).get("composite_score")
        gated = (progress["product"] or {}).get("composite_gated")
        ref = (progress["product"] or {}).get("refinement") or {}
        if gated and not ref.get("ran"):
            return "Final score: build didn't clear the minimum coverage bar — flagged as incomplete rather than scored on style alone."
        if ref.get("refined_grade") is not None:
            tank = (f" Knowledge module {ref['knowledge_module']['tag']} saved to the knowledge base — water tank replenished."
                    if ref.get("water_tank_replenished") else " Refinement didn't beat the original — delivering as-is, no knowledge module saved.")
            return (f"Initial grade: {ref['initial_grade']}/100 (below the 80 bar) — ran the one free refinement loop. "
                    f"Refined grade: {ref['refined_grade']}/100.{tank} Build complete.")
        if ref.get("ran") and ref.get("refined_grade") is None:
            return f"Grade: {ref.get('initial_grade')}/100 (below 80) — starting the one free refinement loop now."
        return f"Done. Final score: {cs} (grade {ref.get('initial_grade')}/100 — cleared the 80 bar first try). Build complete."
    return f"{stage} complete."


async def run_stage(progress: dict) -> dict:
    """Executes exactly the stage at progress['stage_index'], mutates and
    returns `progress` with that stage's outputs filled in, plus `_report`
    (string) and `_done` (bool) for the caller to surface to the user.
    Does NOT advance stage_index for the caller automatically on error —
    raises on unrecoverable failure so server.py can mark the chain failed
    rather than silently getting stuck."""
    seq = progress["stage_sequence"]
    idx = progress["stage_index"]
    stage = seq[idx]
    a = progress["assignments"]
    tier = progress["tier"]
    chain_id = progress["id"]
    user_keys = progress.get("_user_keys")  # injected by server.py per-call (never persisted)

    uses_groq = tier in ("free", "trial") and any(
        v.get("provider") == "groq" for v in a.values() if isinstance(v, dict))
    max_attempts = max(1, _usage.pool_size(tier)) if uses_groq else 1
    last_exc = None
    for attempt in range(max_attempts):
        if uses_groq:
            user_keys = dict(progress.get("_user_keys") or {})
            rotated = _usage.next_groq_key(tier)
            if rotated:
                user_keys["groq"] = rotated
        try:
            if stage == "teacher":
                ts = await teacher_step(progress["target_prompt"], progress["exemplars"],
                                        assignment=a["teacher"], user_keys=user_keys,
                                        chain_id=chain_id, tier=tier,
                                        corrections=progress.get("corrections", ""),
                                        prompt_template=progress.get("teacher_prompt_template"))
                progress["tokens_used"] += ts.pop("_tokens", 0)
                progress["teacher_spec"] = ts

            elif stage == "architect":
                arch = await architect_step(progress["target_prompt"], progress["teacher_spec"],
                                            assignment=a["architect"], user_keys=user_keys,
                                            chain_id=chain_id, tier=tier)
                progress["tokens_used"] += arch.pop("_tokens", 0)
                notes = arch.get("architecture_notes", "")
                progress["_architect_notes"] = notes
                if notes:
                    ts = progress["teacher_spec"]
                    ts["artist_brief"] = f"{ts.get('artist_brief', '')}\n\nARCHITECT GUIDANCE: {notes}".strip()

            elif stage == "artist":
                spec = await artist_step(progress["teacher_spec"], exemplar_files=progress["exemplar_files"],
                                         assignment=a["artist"], user_keys=user_keys,
                                         chain_id=chain_id, tier=tier,
                                         prompt_template=progress.get("artist_prompt_template"))
                progress["tokens_used"] += spec.pop("_tokens", 0)
                progress["artist_spec"] = spec

            elif stage == "execute":
                await _emit(chain_id, "stage", "materialize")
                product = dict(progress["artist_spec"])
                product["target_prompt"] = progress["target_prompt"]
                product["teacher_spec"] = progress["teacher_spec"]
                product["artist_spec"] = {k: v for k, v in progress["artist_spec"].items() if k != "files"}
                product["files"] = seed_template.render(product, stack=product.get("stack") or progress["teacher_spec"].get("stack"))
                stack_l = (product.get("stack") or "").lower()
                exec_timeout = 90 if any(k in stack_l for k in ("node", "vite", "react", "rust", "go")) else 25
                workspace = _exec.materialize(chain_id, product)
                exec_result = await _exec.run_workspace(workspace, timeout=exec_timeout, port_to_check=8123)
                product["exec"] = exec_result
                progress["product"] = product
                progress["exec_result"] = exec_result
                progress["needs_fix"] = not exec_result.get("started")

            elif stage == "reviewer":
                review = await reviewer_step(progress["product"], progress["exec_result"],
                                             assignment=a["reviewer"], user_keys=user_keys,
                                             chain_id=chain_id, tier=tier)
                progress["tokens_used"] += review.pop("_tokens", 0)
                progress["needs_fix"] = progress["needs_fix"] or bool(review.get("needs_fix"))
                progress["review_notes"] = review.get("review_notes", "")
                progress["product"]["reviewer"] = review

            elif stage == "correct":
                # Correction must run whenever the Reviewer/exec flagged an
                # issue — the presence of stderr text is not the trigger,
                # only extra context. Previously this required a non-empty
                # stderr OR review_notes to fire, which silently skipped the
                # correction pass (and then reported "already working") on
                # any failure that produced no stderr — e.g. a SIGTERM
                # (-15) timeout kill or a static build that just never binds
                # a port.
                if progress["needs_fix"]:
                    progress["fallback_used"] = True
                    progress["_correction_ran"] = True
                    corrector_role_key = "corrector" if "corrector" in progress["role_sequence"] else "artist"
                    corrector_assignment = a.get("corrector") if "corrector" in progress["role_sequence"] else a["artist"]
                    product = await correct_step(progress["product"], progress["exec_result"].get("stderr", ""),
                                                 assignment=corrector_assignment, user_keys=user_keys,
                                                 role_key=corrector_role_key, review_notes=progress["review_notes"],
                                                 tier=tier)
                    progress["tokens_used"] += product.pop("_tokens", 0)
                    product["files"] = seed_template.render(product, stack=product.get("stack"))
                    stack_l = (product.get("stack") or "").lower()
                    exec_timeout = 90 if any(k in stack_l for k in ("node", "vite", "react", "rust", "go")) else 25
                    workspace = _exec.materialize(chain_id, product)
                    exec_result = await _exec.run_workspace(workspace, timeout=exec_timeout, port_to_check=8124)
                    product["exec"] = exec_result
                    progress["product"] = product
                    progress["exec_result"] = exec_result
                else:
                    progress["_correction_ran"] = False

            elif stage == "rate":
                scores = await rate_step(progress["product"], progress["exec_result"],
                                         assignment=a["rater"], user_keys=user_keys, tier=tier)
                progress["tokens_used"] += scores.pop("_tokens", 0)
                progress["scores"] = scores
                progress["product"]["reward"] = scores

            elif stage == "score":
                product = progress["product"]
                cov = _coverage.score(progress["target_prompt"], product.get("files") or [])
                product["coverage"] = cov
                if "corrector" in progress["role_sequence"] or "architect" in progress["role_sequence"]:
                    nov = novelty_score(product.get("files") or [], progress["exemplar_files"])
                    weights = progress.get("eval_weights")
                    result = composite_v2(cov.get("coverage"), progress["scores"], nov, weights=weights)
                    product["novelty"] = nov
                    product["composite_gated"] = result["gated"]
                    product["composite_score"] = result["score"]
                    product["eval_weights"] = weights or {"coverage": 0.4, "rater": 0.4, "novelty": 0.2}
                else:
                    base = composite(progress["scores"])
                    product["composite_score"] = (round(base * 0.6 + (cov["coverage"] * 4.0) * 0.4, 3)
                                                  if cov.get("coverage") is not None else base)
                product["gen"] = 1
                product["tier"] = tier
                product["tokens_used"] = progress["tokens_used"]
                product["critic_notes"] = (
                    f"Ran: {progress['exec_result'].get('started')}. "
                    f"Exit: {progress['exec_result'].get('exit_code')}. "
                    f"Port listening: {progress['exec_result'].get('port_listening')}."
                )
                product["assignments"] = {k: a[k] for k in progress["role_sequence"] if k in a}
                product["teacher_variant_id"] = progress.get("teacher_variant_id")
                product["artist_variant_id"] = progress.get("artist_variant_id")
                progress["product"] = product

                # --- Recursive Refinement Loop -------------------------------
                # Pass-fail grade on a 0-100 scale (rescaled from the 0-4
                # composite). >=80 delivers immediately. <80 triggers exactly
                # ONE free refinement loop (re-run artist->execute->correct->
                # rate->score with the weak spots called out), then delivers
                # whatever comes out of that — never more than one extra pass.
                grade = 0 if product.get("composite_gated") else round(max(0.0, min(4.0, product["composite_score"])) / 4.0 * 100)
                if not progress.get("_refinement_ran"):
                    progress["_gen1_grade"] = grade
                    progress["_gen1_tokens"] = progress["tokens_used"]
                    if grade < 80:
                        progress["_gen1_product"] = json.loads(json.dumps(product))
                        progress["_refinement_ran"] = True
                        progress["_loop_back_to"] = "artist"
                        weak = []
                        if not progress["exec_result"].get("started"):
                            weak.append("the build did not run/start at all")
                        s = progress.get("scores") or {}
                        for dim in ("correctness", "helpfulness", "coherence"):
                            if s.get(dim, 4.0) < 2.5:
                                weak.append(f"low {dim} score")
                        if cov.get("coverage") is not None and cov["coverage"] < 0.6:
                            weak.append("missing several features from the original request")
                        weak_txt = "; ".join(weak) or "general code quality and polish"
                        ts = progress["teacher_spec"]
                        ts["artist_brief"] = (
                            f"{ts.get('artist_brief', '')}\n\nREFINEMENT PASS (scored {grade}/100, "
                            f"below the 80 quality bar — this is your one free retry): fix {weak_txt}. "
                            f"Keep everything that already works; only change what's broken or missing."
                        ).strip()
                        product["refinement"] = {
                            "ran": True, "initial_grade": grade, "refined_grade": None,
                            "knowledge_module": None, "water_tank_replenished": False,
                            "base_tokens": progress["_gen1_tokens"], "refinement_tokens": 0,
                        }
                    else:
                        product["refinement"] = {
                            "ran": False, "initial_grade": grade, "refined_grade": None,
                            "knowledge_module": None, "water_tank_replenished": False,
                            "base_tokens": progress["_gen1_tokens"], "refinement_tokens": 0,
                        }
                else:
                    # Second (and final) score pass after the one refinement loop.
                    gen1_grade = progress.get("_gen1_grade", 0)
                    gen1_tokens = progress.get("_gen1_tokens", 0)
                    refinement_tokens = max(0, progress["tokens_used"] - gen1_tokens)
                    improved = grade > gen1_grade
                    knowledge_module = None
                    if improved:
                        knowledge_module = _extract_knowledge_module(
                            progress.get("_gen1_product") or {}, product, progress["target_prompt"],
                        )
                    product["refinement"] = {
                        "ran": True, "initial_grade": gen1_grade, "refined_grade": grade,
                        "knowledge_module": knowledge_module,
                        "water_tank_replenished": bool(knowledge_module),
                        "base_tokens": gen1_tokens, "refinement_tokens": refinement_tokens,
                    }
                progress["product"] = product
                await _emit(chain_id, "stage", "complete")

            last_exc = None
            break
        except ProviderRateLimitedError as exc:
            last_exc = exc
            logger.warning("chain %s stage %s rate-limited on attempt %d/%d — rotating key",
                           chain_id, stage, attempt + 1, max_attempts)
            continue
    if last_exc is not None:
        raise last_exc
    progress["_report"] = _report(stage, progress)
    progress["_stage_ran"] = stage
    loop_back = progress.pop("_loop_back_to", None)
    if loop_back:
        progress["_done"] = False
        progress["stage_index"] = seq.index(loop_back)
    else:
        progress["_done"] = (idx + 1) >= len(seq)
        if not progress["_done"]:
            progress["stage_index"] = idx + 1
    return progress


class ArtistFailedError(Exception):
    """Raised when the Artist cannot produce a real file set after retries."""


async def artist_step(teacher_spec: dict, exemplar_files: Optional[list[dict]] = None,
                      assignment: Optional[dict] = None,
                      user_keys: Optional[dict] = None,
                      chain_id: Optional[str] = None,
                      tier: Optional[str] = None,
                      prompt_template: Optional[str] = None) -> dict:
    parsed: dict = {}
    raw = ""
    exemplar_block = ""
    if exemplar_files:
        # For each exemplar we send the FULL content of its SMALLEST file
        # (usually the main entry — most instructive) plus the full file layout.
        # Total per exemplar is capped so the prompt stays sane.
        MAX_PER_EXEMPLAR = 6000
        parts = []
        for i, ex in enumerate(exemplar_files[:2], 1):
            files_by_size = sorted(
                [f for f in (ex.get("files") or []) if isinstance(f, dict) and f.get("content")],
                key=lambda f: len(f.get("content", "")),
            )
            if not files_by_size:
                continue
            layout = "\n".join(f"  - {f['path']} ({len(f.get('content',''))} chars)"
                               for f in ex.get("files", [])[:15])
            main = files_by_size[0]  # smallest = usually the entry point
            main_content = main.get("content", "")[:MAX_PER_EXEMPLAR]
            parts.append(
                f"### Verified exemplar {i}\n"
                f"target: {ex.get('target','?')}\n"
                f"stack: {ex.get('stack','?')}\n"
                f"file layout:\n{layout}\n"
                f"full content of {main['path']} (copy patterns, adapt to the new target):\n"
                f"```\n{main_content}\n```"
            )
        exemplar_block = "\n\n".join(parts)

    await _emit(chain_id, "stage", "artist")
    artist_tokens = 0
    for attempt in range(3):
        directive = ("Write the source files as strict JSON. Include the FULL content of every "
                     "file — no ellipsis, no TODOs.")
        if attempt == 1:
            directive += (" Your last response was truncated — be TERSER. 3-4 files max, ≤150 lines each."
                          " Prioritize a working minimum over completeness.")
        if attempt == 2:
            directive += (" Third attempt. Output the tiniest possible working version — one main file,"
                          " ≤120 lines, plus run.sh. Skip README, skip niceties.")
        user_msg = f"TEACHER SPEC:\n{json.dumps(teacher_spec, indent=2)}\n\n"
        if exemplar_block:
            user_msg += (
                "PRIOR VERIFIED WORKING BUILDS FOR SIMILAR TARGETS (learn from these file structures):\n"
                f"{exemplar_block}\n\n"
            )
        user_msg += directive
        if attempt > 0:
            await _emit(chain_id, "stage", f"artist-retry-{attempt}")
        # Layer #1 — same template-replaces-system-prompt substitution as
        # teacher_step(), see there for why.
        if prompt_template:
            messages = [{"role": "user", "content": prompt_template.replace("{{USER_INPUT}}", user_msg)}]
        else:
            messages = [{"role": "system", "content": SYSTEM_ARTIST}, {"role": "user", "content": user_msg}]
        raw, call_tokens = await _call_role(
            "artist", messages,
            assignment,
            user_keys=user_keys, tier=tier,
            stream_chain_id=chain_id, stream_event="artist_delta",
            temperature=(0.7, 0.4, 0.2)[attempt],
            max_tokens=(12000, 8000, 4000)[attempt],
        )
        artist_tokens += call_tokens
        parsed = _extract_json(raw or "") or {}
        logger.info("ARTIST attempt=%d raw_len=%d keys=%s files=%d",
                    attempt, len(raw or ""), list(parsed.keys()),
                    len(parsed.get("files") or []))
        if parsed.get("files"):
            break
        if not parsed:
            try:
                Path(f"/tmp/artist_raw_attempt{attempt}.txt").write_text(raw or "")
            except Exception:
                pass

    if not parsed.get("files"):
        raise ArtistFailedError(
            f"Artist produced no usable files after 3 attempts. "
            f"Last response was {len(raw or '')} chars — likely truncated or "
            f"model refused. Try a shorter target prompt or a different Artist model."
        )

    # ensure required fields exist
    parsed.setdefault("name", f"Product-{teacher_spec.get('target','app')[:20]}")
    parsed.setdefault("tagline", "an app built by CapCode")
    parsed.setdefault("philosophy", "Minimal, working, downloadable.")
    parsed.setdefault("improvement_note", "meets the Teacher's brief")
    parsed.setdefault("stack", teacher_spec.get("stack", "python-fastapi"))
    parsed.setdefault("accent_hex", teacher_spec.get("accent_hex", "#7cffb2"))
    parsed.setdefault("accent2_hex", teacher_spec.get("accent2_hex", "#ff79c6"))
    parsed.setdefault("weights", {"helpfulness": 0.35, "correctness": 0.30,
                                  "coherence": 0.20, "complexity": 0.10, "verbosity": -0.05})
    parsed["_tokens"] = artist_tokens
    # Sanitize files array — reject entries missing path or content, drop obvious
    # placeholders, and REJECT any path that tries to escape the workspace via
    # `..`, absolute paths, or backslashes / null bytes (SEC-002). Reuse the
    # shared predicate from `executor` so the guard is identical in every place.
    clean_files: list[dict] = []
    for f in parsed.get("files") or []:
        if not isinstance(f, dict):
            continue
        path = (f.get("path") or "").strip().lstrip("/")
        content = f.get("content")
        if not path or not isinstance(content, str) or not content.strip():
            continue
        if not _exec._is_safe_relpath(path):
            logger.warning("rejected unsafe artist file path: %r", path)
            continue
        if "..." in content and content.count("...") > 3 and len(content) < 200:
            continue  # obvious stub
        clean_files.append({"path": path, "content": content})
    parsed["files"] = clean_files
    return parsed


async def correct_step(spec: dict, stderr: str, assignment: Optional[dict] = None,
                       user_keys: Optional[dict] = None, role_key: str = "artist",
                       review_notes: str = "", tier: Optional[str] = None) -> dict:
    """role_key: which assignment slot to route through. Free tier reuses the
    Artist's own model ("artist"); trial/paid tiers can route through a
    dedicated "corrector" assignment instead."""
    file_dump = "\n\n".join(
        f"### {f['path']}\n```\n{f['content'][:2000]}\n```"
        for f in (spec.get("files") or [])[:10]
    ) or "(no files)"
    prompt = (
        f"Stack: {spec.get('stack')}\nName: {spec.get('name')}\n\n"
        f"CURRENT FILES:\n{file_dump}\n\n"
        f"STDERR (tail):\n{stderr[-1500:]}\n\n"
        + (f"REVIEWER NOTES:\n{review_notes}\n\n" if review_notes else "")
        + "Output the corrected JSON (Artist schema, including the full `files` array)."
    )
    raw, corrector_tokens = await _call_role(
        role_key,
        [{"role": "system", "content": SYSTEM_CORRECTOR},
         {"role": "user", "content": prompt}],
        assignment, user_keys=user_keys, tier=tier, temperature=0.3, max_tokens=6000,
    )
    fixed = _extract_json(raw or "") if raw else {}
    if not fixed:
        spec["_tokens"] = spec.get("_tokens", 0) + corrector_tokens
        return spec
    # merge: corrector's non-file fields overlay spec, corrector's files replace spec's files if present
    merged = {**spec, **{k: v for k, v in fixed.items() if k != "files"}}
    new_files = fixed.get("files")
    if isinstance(new_files, list) and new_files:
        clean = []
        for f in new_files:
            if not (isinstance(f, dict) and f.get("path") and isinstance(f.get("content"), str)):
                continue
            path = f["path"].strip().lstrip("/")
            if not _exec._is_safe_relpath(path):
                logger.warning("rejected unsafe corrector file path: %r", path)
                continue
            clean.append({"path": path, "content": f["content"]})
        if clean:
            merged["files"] = clean
    merged["_tokens"] = spec.get("_tokens", 0) + corrector_tokens
    return merged


async def rate_step(spec: dict, exec_result: dict, assignment: Optional[dict] = None,
                    user_keys: Optional[dict] = None, tier: Optional[str] = None) -> dict:
    brief = json.dumps({
        "name": spec.get("name"), "philosophy": spec.get("philosophy"),
        "improvement_note": spec.get("improvement_note"),
        "exec_started": exec_result.get("started"),
        "exec_error_tail": (exec_result.get("stderr") or "")[-300:],
    }, indent=2)
    raw, rater_tokens = await _call_role(
        "rater",
        [{"role": "system", "content": SYSTEM_RATER},
         {"role": "user", "content": brief}],
        assignment, user_keys=user_keys, tier=tier, temperature=0.1, max_tokens=2000,
    )
    data = _extract_json(raw or "") if raw else {}
    keys = ("helpfulness", "correctness", "coherence", "complexity", "verbosity")

    def _deterministic() -> dict:
        started = bool(exec_result.get("started"))
        base = 2.8 if started else 1.2
        out = {k: base for k in keys}
        out["_tokens"] = rater_tokens
        return out

    if not data:
        return _deterministic()
    # Reject degenerate responses (all-zero, or model parroted an example).
    try:
        values = {k: float(data.get(k, 0.0)) for k in keys}
    except (TypeError, ValueError):
        return _deterministic()
    if all(v == 0.0 for v in values.values()):
        logger.warning("rater returned all-zero — using deterministic fallback")
        return _deterministic()
    # Clamp to spec range so a rogue model can't push composite absurdly high.
    for k in keys:
        values[k] = max(0.0, min(4.0, values[k]))
    values["_tokens"] = rater_tokens
    return values


_KNOWLEDGE_TAG_KEYWORDS = {
    "auth_optimization": ["auth", "login", "jwt", "session", "password", "oauth"],
    "api_throttling": ["rate limit", "throttl", "quota", "429", "backoff"],
    "error_handling": ["error", "exception", "try:", "except", "traceback"],
    "database_access": ["database", "db.", "query", "sql", "mongo", "postgres", "schema"],
    "validation": ["validate", "sanitiz", "pydantic", "required field"],
    "performance": ["cache", "async", "performance", "optimi", "concurren"],
    "security": ["secure", "csrf", "xss", "injection", "encrypt"],
}


def _extract_knowledge_module(gen1_product: dict, gen2_product: dict, target_prompt: str) -> Optional[dict]:
    """'The Tank' — when the one free refinement loop measurably improved the
    build, pull out the file that changed the most between gen1 and gen2 as a
    reusable 'Global Logic Module', tagged by function so future chains can
    draw on it as an exemplar. No extra LLM call — this is a diff over files
    the pipeline already produced, kept cheap on purpose."""
    gen1_files = {f.get("path"): f.get("content", "") for f in (gen1_product.get("files") or []) if isinstance(f, dict)}
    gen2_files = {f.get("path"): f.get("content", "") for f in (gen2_product.get("files") or []) if isinstance(f, dict)}
    changed = [p for p, content in gen2_files.items() if gen1_files.get(p, "") != content]
    if not changed:
        return None
    best_path = max(changed, key=lambda p: len(gen2_files.get(p, "")))
    snippet = gen2_files.get(best_path, "")
    haystack = f"{target_prompt} {best_path} {snippet}".lower()
    tag = next((t for t, kws in _KNOWLEDGE_TAG_KEYWORDS.items() if any(kw in haystack for kw in kws)), "general_fix")
    return {
        "tag": f"#{tag}",
        "source_path": best_path,
        "code": snippet[:4000],
        "target_prompt": target_prompt,
    }


def composite(scores: dict) -> float:
    return round(
        0.35 * scores.get("helpfulness", 0)
        + 0.30 * scores.get("correctness", 0)
        + 0.20 * scores.get("coherence", 0)
        + 0.10 * scores.get("complexity", 0)
        - 0.05 * abs(scores.get("verbosity", 2) - 2.0),
        3,
    )


# ---------------------------------------------------------------------------
# composite_v2 — coverage-gated score with a novelty bonus for the 6-role
# (trial/paid) pipeline. Does NOT replace composite() above, which is still
# used for the free-tier 2-role pipeline's rater-only scoring.
#
#   composite_v2 = coverage*0.4 + rater*0.4 + novelty*0.2
#   HARD GATE: if coverage < COVERAGE_GATE_THRESHOLD, score is 0 before the
#   weighted formula ever runs — a creative-but-broken build cannot pass on
#   novelty alone.
# ---------------------------------------------------------------------------
COVERAGE_GATE_THRESHOLD = 0.5


def _word_set(text: str) -> set:
    return set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", (text or "").lower()))


def novelty_score(files: list[dict], exemplar_files: Optional[list[dict]]) -> float:
    """Novelty relative to the 'expected' solution — approximated here as the
    nearest prior verified exemplar for a similar target (already loaded by
    the caller, no extra LLM call needed). 1.0 = maximally different from
    what's been built before for similar prompts, 0.0 = near-identical.
    Falls back to a neutral 0.5 when there's nothing to compare against
    (first-ever build in a category shouldn't be penalized OR rewarded)."""
    if not exemplar_files:
        return 0.5
    new_words = _word_set("\n".join(f.get("content", "") for f in files if isinstance(f, dict)))
    if not new_words:
        return 0.5
    best_similarity = 0.0
    for ex in exemplar_files[:3]:
        ex_words = _word_set("\n".join(f.get("content", "") for f in (ex.get("files") or [])
                                        if isinstance(f, dict)))
        if not ex_words:
            continue
        inter = len(new_words & ex_words)
        union = len(new_words | ex_words)
        similarity = (inter / union) if union else 0.0
        best_similarity = max(best_similarity, similarity)
    return round(max(0.0, min(1.0, 1.0 - best_similarity)), 3)


def composite_v2(coverage: Optional[float], rater_scores: dict, novelty: float,
                 weights: Optional[dict] = None) -> dict:
    """Returns {"score": float 0-4ish, "gated": bool, "coverage": float, "novelty": float}.
    rater_scores is the same dict rate_step() returns (helpfulness/correctness/etc) —
    we normalize its composite() output (roughly 0-4 scale) down to 0-1 to combine
    with coverage/novelty, then scale the final blended score back to 0-4 for
    consistency with the existing composite()'s range.
    `weights` (coverage/rater/novelty, summing to ~1.0) defaults to the
    original fixed 0.4/0.4/0.2 split — Layer #5's meta-loop (eval_meta.py)
    supplies a data-driven set here once there's enough outcome signal."""
    w = weights or {"coverage": 0.4, "rater": 0.4, "novelty": 0.2}
    rater_norm = max(0.0, min(1.0, composite(rater_scores) / 4.0))
    cov = coverage if coverage is not None else 0.5  # neutral when no deterministic signal
    if cov < COVERAGE_GATE_THRESHOLD:
        return {"score": 0.0, "gated": True, "coverage": cov, "novelty": novelty}
    # .get() with a default, never w[...] — a partial/malformed eval_weights
    # doc must degrade to the safe default for that one dimension, not crash
    # the whole score stage.
    blended = (cov * w.get("coverage", 0.4) + rater_norm * w.get("rater", 0.4)
              + novelty * w.get("novelty", 0.2))
    return {"score": round(blended * 4.0, 3), "gated": False, "coverage": cov, "novelty": novelty}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
async def evolve_chain_with_callback(
    chain_id: str,
    target_prompt: str,
    on_gen,
    on_done,
    assignments: Optional[dict] = None,
    exemplars: Optional[list[dict]] = None,
    exemplar_files: Optional[list[dict]] = None,
    user_keys: Optional[dict] = None,
    tier: str = "free",
) -> dict:
    """One chain = one product build.
       free tier  (2 roles): Teacher -> Artist -> Executor (+correction) -> Rater.
       trial/paid (6 roles): Teacher -> Architect -> Artist -> Executor ->
                              Reviewer -> (Corrector if flagged) -> Rater -> novelty.
    Raises ArtistFailedError if the Artist can't produce real files after retries.

    Caller (server.py) is expected to have already run usage.check_budget()
    for this tier/user BEFORE calling this — this function does not itself
    gate on budget, it just reports tokens_used back in `final` so the caller
    can log it to the usage ledger after the fact.
    """
    role_sequence = _providers.role_sequence_for_tier(tier)
    a = dict(_providers.assignments_for_tier(tier))
    for role in role_sequence:
        if assignments and assignments.get(role):
            a[role] = assignments[role]

    # GROQ KEY ROTATION — free/trial tiers route through our own rotating
    # Groq key pools (kept separate so trial traffic never starves the free
    # pool). Reuses the existing BYOK plumbing: _client_for always builds a
    # fresh client when user_keys has an entry for the provider, so injecting
    # a rotated key here transparently swaps the key used for every Groq
    # call in this chain without touching _call_role/_chat/_client_for.
    if tier in ("free", "trial") and any(v.get("provider") == "groq" for v in a.values() if isinstance(v, dict)):
        user_keys = dict(user_keys or {})
        rotated = _usage.next_groq_key(tier)
        if rotated:
            user_keys["groq"] = rotated

    exemplars = exemplars or []
    fallback_used = False
    tokens_used = 0

    try:
        # 1) TEACHER (always runs)
        teacher_spec = await teacher_step(target_prompt, exemplars,
                                          assignment=a["teacher"],
                                          user_keys=user_keys,
                                          chain_id=chain_id, tier=tier)
        tokens_used += teacher_spec.pop("_tokens", 0)

        # 1b) ARCHITECT (trial/paid only) — folds structural guidance into the
        # Artist's brief before the Artist ever runs.
        if "architect" in role_sequence:
            arch = await architect_step(target_prompt, teacher_spec,
                                        assignment=a["architect"],
                                        user_keys=user_keys, chain_id=chain_id, tier=tier)
            tokens_used += arch.pop("_tokens", 0)
            if arch.get("architecture_notes"):
                teacher_spec["artist_brief"] = (
                    f"{teacher_spec.get('artist_brief', '')}\n\n"
                    f"ARCHITECT GUIDANCE: {arch['architecture_notes']}"
                ).strip()

        # 2) ARTIST — writes the real source files. Raises if all attempts truncate.
        artist_spec = await artist_step(teacher_spec, exemplar_files=exemplar_files,
                                        assignment=a["artist"],
                                        user_keys=user_keys,
                                        chain_id=chain_id, tier=tier)
        tokens_used += artist_spec.pop("_tokens", 0)

        # 3) BUILD PRODUCT
        await _emit(chain_id, "stage", "materialize")
        product = dict(artist_spec)
        product["target_prompt"] = target_prompt
        product["teacher_spec"] = teacher_spec
        product["artist_spec"] = {k: v for k, v in artist_spec.items() if k != "files"}
        product["files"] = seed_template.render(product, stack=product.get("stack") or teacher_spec.get("stack"))

        # 4) EXECUTOR — longer timeout for stacks that need install/compile.
        await _emit(chain_id, "stage", "execute")
        stack_l = (product.get("stack") or "").lower()
        exec_timeout = 90 if any(k in stack_l for k in ("node", "vite", "react", "rust", "go")) else 25
        workspace = _exec.materialize(chain_id, product)
        exec_result = await _exec.run_workspace(workspace, timeout=exec_timeout, port_to_check=8123)
        product["exec"] = exec_result

        # 4b) REVIEWER (trial/paid only) — can flag a correction pass even on
        # a build that technically started but doesn't satisfy the target.
        needs_fix = not exec_result.get("started")
        review_notes = ""
        if "reviewer" in role_sequence:
            review = await reviewer_step(product, exec_result, assignment=a["reviewer"],
                                         user_keys=user_keys, chain_id=chain_id, tier=tier)
            tokens_used += review.pop("_tokens", 0)
            needs_fix = needs_fix or bool(review.get("needs_fix"))
            review_notes = review.get("review_notes", "")
            product["reviewer"] = review

        # 5) CORRECTION pass whenever execution failed OR reviewer flagged it —
        # not gated on stderr/review_notes being non-empty (a SIGTERM/-15
        # timeout or a static build that never binds a port has no stderr,
        # but still needs the correction pass to actually run).
        if needs_fix:
            fallback_used = True
            await _emit(chain_id, "stage", "corrector")
            corrector_role_key = "corrector" if "corrector" in role_sequence else "artist"
            corrector_assignment = a.get("corrector") if "corrector" in role_sequence else a["artist"]
            product = await correct_step(product, exec_result.get("stderr", ""),
                                         assignment=corrector_assignment, user_keys=user_keys,
                                         role_key=corrector_role_key, review_notes=review_notes,
                                         tier=tier)
            tokens_used += product.pop("_tokens", 0)
            product["files"] = seed_template.render(product, stack=product.get("stack"))
            workspace = _exec.materialize(chain_id, product)
            exec_result = await _exec.run_workspace(workspace, timeout=exec_timeout, port_to_check=8124)
            product["exec"] = exec_result

        # 6) RATER (always runs)
        await _emit(chain_id, "stage", "rater")
        scores = await rate_step(product, exec_result, assignment=a["rater"], user_keys=user_keys, tier=tier)
        tokens_used += scores.pop("_tokens", 0)
        product["reward"] = scores

        # 6b) Deterministic feature-coverage — no LLM call, regex-based.
        cov = _coverage.score(target_prompt, product.get("files") or [])
        product["coverage"] = cov

        if "corrector" in role_sequence or "architect" in role_sequence:
            # Full 6-role pipeline: coverage-gated composite_v2 with novelty.
            nov = novelty_score(product.get("files") or [], exemplar_files)
            result = composite_v2(cov.get("coverage"), scores, nov)
            product["novelty"] = nov
            product["composite_gated"] = result["gated"]
            product["composite_score"] = result["score"]
        else:
            # Free tier: original 2-role blend (unchanged behavior).
            base = composite(scores)
            if cov.get("coverage") is not None:
                product["composite_score"] = round(base * 0.6 + (cov["coverage"] * 4.0) * 0.4, 3)
            else:
                product["composite_score"] = base

        product["gen"] = 1
        product["tier"] = tier
        product["tokens_used"] = tokens_used
        product["critic_notes"] = (
            f"Ran: {exec_result.get('started')}. "
            f"Exit: {exec_result.get('exit_code')}. "
            f"Port listening: {exec_result.get('port_listening')}."
        )
        product["assignments"] = {k: a[k] for k in role_sequence if k in a}

        await on_gen(chain_id, product, fallback_used)

        final = {
            "id": chain_id,
            "target_prompt": target_prompt,
            "depth": 1,
            "fallback_used": fallback_used,
            "generations": [product],
            "tier": tier,
            "tokens_used": tokens_used,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await on_done(chain_id, final)
        await _emit(chain_id, "stage", "complete")
        return final
    finally:
        close_stream(chain_id)
