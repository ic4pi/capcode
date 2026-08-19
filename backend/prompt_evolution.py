"""
prompt_evolution.py — Layer #1: recursive self-improvement for the Teacher's
own system prompt. Same loop shape as eval_meta.py (#5): act -> measure
against real outcomes -> adjust the thing that acted -> repeat. Here the
thing being adjusted is SYSTEM_TEACHER itself, not the scoring weights.

Guardrails (explicit product spec):
  - A/B, never overwrite. A challenger prompt runs alongside the champion
    on real traffic and only replaces it after actually beating it — the
    old champion is kept forever as status="retired", never deleted.
  - Segmented by project type. Segmenting on the Teacher's own `stack`
    output would be circular (the Teacher hasn't run yet when we need to
    pick a prompt), so a build is bucketed from keywords in the human's
    target_prompt BEFORE the Teacher ever sees it.
  - Rule-based template only. Every variant — seed or machine-generated —
    is required to keep the strict [ROLE]/[CONSTRAINTS]/[NEGATIONS]/
    [EXAMPLES]/[DATA] structure, no soft qualifiers, no preamble. A
    generated candidate that breaks this shape is rejected outright and
    never becomes eligible to run.
"""
from __future__ import annotations

import logging
import random
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import providers as _providers
import edits as _edits

logger = logging.getLogger("prompt_evolution")

MIN_SAMPLE_SIZE = 15
THROTTLE_SEC = 3600
NEEDS_IMPROVEMENT_EDIT_RATE = 0.25   # champion is "fine" below this — leave it alone
PROMOTE_MARGIN = 0.85                # challenger must cut the edit rate to <=85% of champion's to win
CANDIDATE_MODEL = ("nvidia", "z-ai/glm-5.2")

SEGMENT_KEYWORDS = {
    "frontend": ["react", "vue", "landing page", "website", "webpage", "ui ", "frontend", "portfolio"],
    "backend":  ["api", "backend", "server", "database", "endpoint", "microservice", "webhook"],
    "cli":      ["cli", "command-line", "command line", "script", "automation tool", "terminal"],
    "game":     ["game", "arcade", "puzzle", "platformer"],
}
DEFAULT_SEGMENT = "other"

REQUIRED_SECTIONS = ["[ROLE:", "[CONSTRAINTS:", "[NEGATIONS:", "[EXAMPLES:", "[DATA:"]
FORBIDDEN_PHRASES = ["please", "try to", "hopefully", "i hope this helps", "maybe you could"]

# Seed champions — must satisfy validate_template() themselves. Duplicated
# from chain_generator.py's SYSTEM_TEACHER/SYSTEM_ARTIST on purpose (same
# reason eval_meta.py duplicates the rater formula): chain_generator imports
# this module for get_active_variant(), so this module must not import
# chain_generator back.
SEED_TEACHER_TEMPLATE = """[ROLE: You are the TEACHER, Bot 1 in CapCode's two-bot chain. You are rigid, strict, and provider-educated. Given a human's target app description, you must design a build spec for the ARTIST bot that will construct it. You must not design the product yourself — you must design the second bot's brief only.]

[CONSTRAINTS:
- You must detect the correct stack from the target: python-fastapi, node-vite, rust, go, or webgl. Default to python-fastapi when the stack is unclear.
- You must study any TOP-VERIFIED PRIOR CHAINS provided and reuse their proven patterns.
- You must output STRICT JSON only, matching exactly this schema:
{
  "name": "TeacherSpec-<short-name>",
  "target": "<echo of the human's target>",
  "stack": "python-fastapi | node-vite | rust | go | webgl",
  "artist_brief": "3-5 sentences briefing the Artist on what to build",
  "must_have": ["3-5 concrete requirements the product must satisfy"],
  "should_avoid": ["2-3 anti-patterns"],
  "accent_hex": "#rrggbb",
  "accent2_hex": "#rrggbb"
}
- Every requirement in "must_have" must be concrete and testable, not vague.]

[NEGATIONS:
- Never output prose, explanation, or markdown fencing before or after the JSON object.
- Never design the product's source code — that is the Artist's job.
- Never invent a stack outside the five listed options.
- Never repeat a prior correction listed under COMMON USER CORRECTIONS.]

[EXAMPLES:
Input: "a todo list app" | Output: {"name": "TeacherSpec-TodoList", "target": "a todo list app", "stack": "node-vite", "artist_brief": "Build a single-page React todo list: add, complete, and delete tasks, persisted to localStorage. Keep the UI minimal and fast.", "must_have": ["add task", "mark complete", "delete task", "persists across reload"], "should_avoid": ["backend server for a client-only app"], "accent_hex": "#7cffb2", "accent2_hex": "#ff79c6"}]

[DATA: ###{{USER_INPUT}}###]"""

SEED_ARTIST_TEMPLATE = """[ROLE: You are the ARTIST, Bot 2 in CapCode's two-bot chain. You are creative, sharp, and novel. Given the Teacher's spec, you must write the actual source code for the app the human asked for. You do not describe. You do not hand-wave. You write real, runnable files.]

[CONSTRAINTS:
- Every string in files[].content must be complete, real, runnable source code.
- The app must start with a single `bash run.sh` and must satisfy every must_have in the Teacher spec.
- Stack-specific requirements:
  python-fastapi: include backend/server.py (a real FastAPI app with a working /api route), backend/requirements.txt, run.sh (uvicorn on $APP_PORT).
  node-vite: include src/main.jsx (real React with real state/fetch/UI), index.html, package.json (correct deps), vite.config.js, run.sh (npm install + vite --host --port $APP_PORT).
  rust: include Cargo.toml, src/main.rs (real logic), run.sh (cargo run).
  go: include go.mod, main.go (real logic), run.sh (go run main.go).
  webgl: include a single self-contained index.html with real inline JS, run.sh (python3 -m http.server $APP_PORT).
- Any public API call (prices, weather, quotes, jokes, etc.) must use a well-known keyless public endpoint (CoinGecko, wttr.in, jsonplaceholder, etc.).
- You must output STRICT JSON only, matching exactly this schema:
{
  "name": "PascalCase product name",
  "tagline": "one line",
  "philosophy": "2 sentences — your creative angle",
  "improvement_note": "how you satisfied the Teacher's must_have while adding novelty",
  "stack": "<echo the teacher's stack>",
  "accent_hex": "#rrggbb",
  "accent2_hex": "#rrggbb",
  "files": [{"path": "relative/path.ext", "content": "FULL FILE CONTENT"}],
  "weights": {"helpfulness":0.35,"correctness":0.30,"coherence":0.20,"complexity":0.10,"verbosity":-0.05}
}]

[NEGATIONS:
- Never use "..." ellipsis, "TODO" stubs, or placeholder content anywhere in files[].content.
- Never invent an API endpoint that does not exist.
- Never output prose before or after the JSON object.
- Never import a package that was not declared in the dependency file.]

[EXAMPLES:
Input: {"stack": "webgl", "artist_brief": "a single button that shows a random quote"} | Output: {"name": "QuoteFlash", "tagline": "one tap, one quote", "philosophy": "Instant gratification, zero chrome.", "improvement_note": "used a keyless public quotes API with a fallback local list", "stack": "webgl", "accent_hex": "#7cffb2", "accent2_hex": "#ff79c6", "files": [{"path": "index.html", "content": "FULL HTML+JS HERE"}, {"path": "run.sh", "content": "#!/bin/bash\\npython3 -m http.server $APP_PORT"}], "weights": {"helpfulness":0.35,"correctness":0.30,"coherence":0.20,"complexity":0.10,"verbosity":-0.05}}]

[DATA: ###{{USER_INPUT}}###]"""


def infer_segment(target_prompt: str) -> str:
    text = (target_prompt or "").lower()
    for seg, kws in SEGMENT_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return seg
    return DEFAULT_SEGMENT


def validate_template(text: str) -> tuple[bool, str]:
    if not text or len(text) < 50:
        return False, "empty or too short"
    for section in REQUIRED_SECTIONS:
        if section not in text:
            return False, f"missing required section {section}"
    lowered = text.lower()
    for phrase in FORBIDDEN_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            return False, f"contains forbidden soft qualifier: '{phrase}'"
    return True, ""


async def ensure_seed(db, role: str, template: str) -> None:
    """Idempotent — seeds the global champion once. A no-op if it already
    exists, so a restart/hot-reload never clobbers a promoted challenger
    with the original seed text."""
    existing = await db.prompt_variants.find_one({"role": role, "segment": "global", "status": "champion"})
    if existing:
        return
    await db.prompt_variants.insert_one({
        "id": str(uuid.uuid4()), "role": role, "segment": "global", "version": 1,
        "template": template, "status": "champion",
        "created_at": datetime.now(timezone.utc).isoformat(), "created_by": "seed",
    })


async def get_active_variant(db, role: str, segment: str, fallback_template: str) -> dict:
    """Picks what prompt THIS build should use. A segment with its own
    champion wins; otherwise falls back to the global champion. If a
    challenger is running for this segment, it gets a 50/50 shot at real
    traffic — that's the 'A/B, don't overwrite' guardrail in practice."""
    champion = await db.prompt_variants.find_one({"role": role, "segment": segment, "status": "champion"})
    if not champion:
        champion = await db.prompt_variants.find_one({"role": role, "segment": "global", "status": "champion"})
    if not champion:
        return {"id": None, "template": fallback_template, "segment": segment}
    challenger = await db.prompt_variants.find_one({"role": role, "segment": segment, "status": "challenger"})
    if challenger and random.random() < 0.5:
        return {"id": challenger["id"], "template": challenger["template"], "segment": segment}
    return {"id": champion["id"], "template": champion["template"], "segment": segment}


async def _variant_stats(db, role: str, variant_id: str) -> dict:
    chains = await db.chains.find({f"{role}_variant_id": variant_id, "status": "complete"}).to_list(500)
    build_count = len(chains)
    if build_count == 0:
        return {"build_count": 0, "edit_rate": 1.0, "avg_score": 0.0}
    diff_rows = await db.edit_diffs.find({}).to_list(5000)
    corrected_ids = {r.get("chain_id") for r in diff_rows}
    corrected = sum(1 for c in chains if c["id"] in corrected_ids)
    scores = [c["generations"][-1].get("composite_score", 0) for c in chains if c.get("generations")]
    return {
        "build_count": build_count,
        "edit_rate": corrected / build_count,
        "avg_score": (sum(scores) / len(scores)) if scores else 0.0,
    }


async def _generate_candidate(champion_template: str, corrections: str, stats: dict) -> Optional[str]:
    provider, model = CANDIDATE_MODEL
    client = _providers.openai_client_for(provider)
    if not client:
        return None
    edit_pct = round(stats["edit_rate"] * 100)
    prompt = (
        f"CURRENT TEACHER PROMPT (edit rate {edit_pct}% across {stats['build_count']} builds — too high, needs improvement):\n"
        f"###\n{champion_template}\n###\n\n"
        f"COMMON USER CORRECTIONS THESE BUILDS NEEDED:\n###\n{corrections or '(none recorded)'}\n###\n\n"
        "Rewrite the prompt above so future builds need fewer corrections. "
        "You must keep the exact structural shape: a [ROLE: ...] section, a [CONSTRAINTS: ...] section, "
        "a [NEGATIONS: ...] section, an [EXAMPLES: ...] section, and a final [DATA: ###{{...}}###] section. "
        "You must not use soft qualifiers such as please, try to, maybe, or hopefully. "
        "You must not add any preamble before or after the prompt text. "
        "Only change what the corrections above justify changing — leave everything else as-is. "
        "Return only the rewritten prompt text, nothing else."
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1200,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as exc:
        logger.warning("prompt_evolution candidate generation failed: %s", exc)
        return None


async def maybe_evolve(db, role: str, segment: str) -> None:
    """Opportunistic, throttled trigger — safe to call after every build
    completion and every new edit-diff detection. The real work runs at
    most once an hour per (role, segment), never raises."""
    try:
        meta_id = f"{role}:{segment}"
        meta = await db.prompt_evo_meta.find_one({"id": meta_id})
        if meta and meta.get("checked_at"):
            try:
                age = time.time() - datetime.fromisoformat(meta["checked_at"]).timestamp()
                if age < THROTTLE_SEC:
                    return
            except Exception:
                pass
        await _evolve(db, role, segment, meta_id)
    except Exception as exc:
        logger.warning("prompt_evolution.maybe_evolve failed: %s", exc)


async def _evolve(db, role: str, segment: str, meta_id: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.prompt_evo_meta.update_one({"id": meta_id}, {"$set": {"id": meta_id, "checked_at": now_iso}}, upsert=True)

    champion = await db.prompt_variants.find_one({"role": role, "segment": segment, "status": "champion"})
    if not champion:
        champion = await db.prompt_variants.find_one({"role": role, "segment": "global", "status": "champion"})
    if not champion:
        return

    challenger = await db.prompt_variants.find_one({"role": role, "segment": segment, "status": "challenger"})
    if challenger:
        cstats = await _variant_stats(db, role, challenger["id"])
        if cstats["build_count"] >= MIN_SAMPLE_SIZE:
            champ_stats = await _variant_stats(db, role, champion["id"])
            if cstats["edit_rate"] <= champ_stats["edit_rate"] * PROMOTE_MARGIN:
                await db.prompt_variants.update_one({"id": champion["id"]}, {"$set": {"status": "retired"}})
                await db.prompt_variants.update_one({"id": challenger["id"]}, {"$set": {"status": "champion", "segment": segment}})
                logger.info("prompt_evolution: promoted challenger %s over champion %s (segment=%s)",
                           challenger["id"], champion["id"], segment)
            else:
                await db.prompt_variants.update_one({"id": challenger["id"]}, {"$set": {"status": "retired"}})
                logger.info("prompt_evolution: challenger %s did not beat champion, retired", challenger["id"])
        return  # only one challenger in flight per segment at a time

    stats = await _variant_stats(db, role, champion["id"])
    if stats["build_count"] < MIN_SAMPLE_SIZE or stats["edit_rate"] <= NEEDS_IMPROVEMENT_EDIT_RATE:
        return

    corrections = await _edits.corrections_digest(db, limit=5)
    candidate_text = await _generate_candidate(champion["template"], corrections, stats)
    if not candidate_text:
        return
    ok, reason = validate_template(candidate_text)
    if not ok:
        logger.warning("prompt_evolution: rejected auto-generated candidate (%s)", reason)
        return

    await db.prompt_variants.insert_one({
        "id": str(uuid.uuid4()), "role": role, "segment": segment,
        "version": champion.get("version", 1) + 1, "template": candidate_text,
        "status": "challenger", "challenging_variant_id": champion["id"],
        "created_at": now_iso, "created_by": "auto",
    })
    logger.info("prompt_evolution: new challenger generated for segment=%s (champion edit_rate=%.2f)",
               segment, stats["edit_rate"])
