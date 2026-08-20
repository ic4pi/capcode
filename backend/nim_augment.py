"""
NVIDIA NIM augmentation layer. Three models enhance each deterministic build:
  - GLM-5.1                 → writes a real DESIGN.md file
  - MiniMax-M2.7            → writes a prose critique
  - Nemotron-70B-Reward     → scores the real generated code
Each call returns None on failure; callers must have a heuristic fallback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger("nim")

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "").strip()
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
GLM_MODEL = "z-ai/glm-5.1"
MINIMAX_MODEL = "minimaxai/minimax-m2.7"
# NOTE: nvidia/llama-3.1-nemotron-70b-reward was retired from the public catalog;
# nemotron-4-340b-reward is gated behind a paid account tier.
# We use Nemotron-Super-49B v1.5 as a reasoning-based reward judge — same lineage,
# returns clean JSON when prompted with a strict rubric.
REWARD_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"

_client = None
ENABLED = False

if NVIDIA_API_KEY:
    try:
        from openai import AsyncOpenAI

        _client = AsyncOpenAI(
            api_key=NVIDIA_API_KEY,
            base_url=NIM_BASE_URL,
            timeout=75.0,
            max_retries=1,
        )
        ENABLED = True
        logger.info("NIM augmentation enabled.")
    except Exception as exc:  # pragma: no cover
        logger.warning("NIM init failed: %s", exc)
else:
    logger.info("NVIDIA_API_KEY empty — NIM augmentation disabled.")


def _project_brief(proj: dict, max_files: int = 4, max_lines: int = 30) -> str:
    """Compact representation of a generated project to fit in LLM context."""
    bot = proj.get("bot", {})
    app = proj.get("app", {})
    lines = [
        f"# {app.get('name','app')} ({bot.get('domain','?')})",
        f"tagline: {app.get('tagline','')}",
        "entities:",
    ]
    for e in app.get("entities", []):
        fields = ", ".join(f"{f['name']}:{f['type']}" for f in e.get("fields", []))
        lines.append(f"  - {e['name']}({fields})")
    lines.append("endpoints:")
    for ep in app.get("endpoints", []):
        lines.append(f"  {ep['method']:6} {ep['path']:40} — {ep.get('purpose','')}")
    lines.append("\n## File previews")
    for f in proj.get("files", [])[:max_files]:
        head = "\n".join(f["content"].splitlines()[:max_lines])
        lines.append(f"\n--- {f['path']} (head) ---\n{head}")
    return "\n".join(lines)


async def _chat(model: str, messages: list[dict], **kwargs) -> Optional[str]:
    if not ENABLED or _client is None:
        return None
    try:
        resp = await _client.chat.completions.create(
            model=model, messages=messages, **kwargs
        )
        msg = resp.choices[0].message
        # Reasoning models (e.g. MiniMax M2.7) put output in reasoning_content when
        # content is None. Prefer content; fall back to reasoning_content's last paragraph.
        content = (msg.content or "").strip()
        if not content:
            reasoning = getattr(msg, "reasoning_content", None) or ""
            if reasoning:
                # last non-empty paragraph is usually the conclusion
                paras = [p.strip() for p in reasoning.split("\n\n") if p.strip()]
                content = paras[-1] if paras else reasoning.strip()
        return content
    except Exception as exc:
        logger.error("NIM %s call failed: %s", model, exc)
        return None


async def design_doc(prompt: str, proj: dict) -> Optional[str]:
    """GLM-5.1 writes a real DESIGN.md for the produced project."""
    brief = _project_brief(proj)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior engineer documenting a tiny FastAPI + vanilla-JS app for new "
                "contributors. Output GitHub-flavoured Markdown (no code fences for the whole "
                "doc). Sections: 'Overview', 'Architecture', 'Data Model', 'API Surface', "
                "'How to run', 'Where to extend'. Be concrete and short — 250-400 words total."
            ),
        },
        {
            "role": "user",
            "content": f"Original prompt: {prompt!r}\n\nProject:\n{brief}",
        },
    ]
    return await _chat(GLM_MODEL, messages, temperature=0.4, max_tokens=800)


async def critique(prompt: str, proj: dict) -> Optional[str]:
    """MiniMax-M2.7 writes a real prose critique. (Reasoning model: needs headroom.)"""
    brief = _project_brief(proj)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a code reviewer. Given a small generated app, identify 2-4 concrete "
                "weaknesses (missing tests, unsafe patterns, missing endpoints, schema gaps, "
                "UX issues). Be specific and actionable. Output a single paragraph, no preamble, "
                "no markdown. Under 120 words. Final answer only — no internal monologue."
            ),
        },
        {"role": "user", "content": f"Prompt: {prompt!r}\n\n{brief}"},
    ]
    return await _chat(MINIMAX_MODEL, messages, temperature=0.6, max_tokens=1500)


async def reward_score(prompt: str, proj: dict) -> Optional[dict]:
    """Nemotron-Super-49B v1.5 acts as a reasoning-based reward model and returns
    5-attribute scores in strict JSON.

    (The original Nemotron-70B-Reward endpoint was retired from the public catalog;
    Nemotron-Super-49B is the actively-hosted reasoning variant in the same family.)
    """
    brief = _project_brief(proj, max_files=3, max_lines=25)
    rubric = (
        "You are a strict JSON-only reward scorer. Score the generated app on 5 "
        "dimensions, each 0.0-4.0:\n"
        "  - helpfulness: does it solve the user's stated request?\n"
        "  - correctness: are endpoints/entities well-typed and consistent?\n"
        "  - coherence: do backend, frontend, and data model agree?\n"
        "  - complexity: appropriate depth (not too simple, not bloated)\n"
        "  - verbosity: target ~2.0 (≈250 LOC); penalize both sparse and bloated\n"
        "Output ONLY this JSON, no prose, no code fences:\n"
        '{"helpfulness":0.0,"correctness":0.0,"coherence":0.0,"complexity":0.0,"verbosity":0.0}'
    )
    messages = [
        {"role": "system", "content": rubric},
        {"role": "user", "content": f"Prompt: {prompt!r}\n\n{brief}"},
    ]
    raw = await _chat(REWARD_MODEL, messages, temperature=0.1, max_tokens=4000)
    if not raw:
        return None
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not m:
        logger.warning("Nemotron judge returned no JSON: %r", raw[:200])
        return None
    try:
        data = json.loads(m.group(0))
    except Exception as exc:
        logger.warning("Nemotron judge JSON parse failed: %s — %r", exc, m.group(0)[:200])
        return None
    keys = ("helpfulness", "correctness", "coherence", "complexity", "verbosity")
    return {k: float(data.get(k, 0.0)) for k in keys}


async def augment(prompt: str, proj: dict) -> dict:
    """Run all three augmentations concurrently. Returns dict of what succeeded."""
    if not ENABLED:
        return {}
    design, crit, scores = await asyncio.gather(
        design_doc(prompt, proj),
        critique(prompt, proj),
        reward_score(prompt, proj),
        return_exceptions=False,
    )
    out: dict = {}
    if design:
        out["design"] = design
    if crit:
        out["critique"] = crit.strip()
    if scores:
        out["scores"] = scores
    return out
