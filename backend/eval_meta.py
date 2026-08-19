"""
eval_meta.py — recursive self-improvement Layer #5: the Evaluation-Criteria
Meta-Loop. Nothing else in the app can improve past whatever this rewards,
so the rubric itself has to learn too, not just stay a fixed formula.

Today's composite_v2 formula (chain_generator.py) is:
    score = coverage*0.4 + rater*0.4 + novelty*0.2
This module periodically recomputes those three weights from REAL outcomes
instead of leaving them as permanent guesses:
  - "good" build  = user clicked ✓ Verify AND no correction was ever detected
                     for it (edit_diffs, Layer #7)
  - "bad"  build   = anything else that reached "complete"
For each dimension (coverage/rater/novelty), the gap between its average
value on good vs. bad builds is how much that dimension actually predicts
quality. Dimensions with a bigger gap earn more weight next time.

Guardrails: a minimum sample size before touching the defaults (early on
there just isn't enough signal), a floor per weight (no dimension ever hits
zero and gets ignored), and blending 70% of the previous weights with only
30% of the freshly-computed ones so the rubric drifts, it doesn't lurch.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("eval_meta")

DEFAULT_WEIGHTS = {"coverage": 0.4, "rater": 0.4, "novelty": 0.2}
MIN_SAMPLE_SIZE = 15          # don't touch the rubric with less signal than this
RECOMPUTE_THROTTLE_SEC = 3600  # at most once an hour, even if called every request
MAX_CHAINS_SCANNED = 500       # bounded scan — this is a periodic heuristic, not a report

_WEIGHTS_ID = "global"


def _rater_norm(scores: dict) -> float:
    """Same formula as chain_generator.composite(), duplicated on purpose —
    eval_meta.py must not import chain_generator (chain_generator imports
    this module for get_weights(), and a two-way import would be circular)."""
    raw = (
        0.35 * scores.get("helpfulness", 0)
        + 0.30 * scores.get("correctness", 0)
        + 0.20 * scores.get("coherence", 0)
        + 0.10 * scores.get("complexity", 0)
        - 0.05 * abs(scores.get("verbosity", 2) - 2.0)
    )
    return max(0.0, min(1.0, raw / 4.0))


async def get_weights(db) -> dict:
    """Read-only, cheap — always returns a usable weight set even if nothing
    has been computed yet (falls back to DEFAULT_WEIGHTS). `is_default` is
    keyed off `updated_at` (only ever set once a real recompute has enough
    sample size), not just whether a doc exists — a below-threshold
    'checked but not learned yet' record must not claim to be data-driven."""
    doc = await db.eval_weights.find_one({"id": _WEIGHTS_ID})
    if not doc or not doc.get("weights") or not doc.get("updated_at"):
        return {"weights": (doc or {}).get("weights", DEFAULT_WEIGHTS),
                "sample_size": (doc or {}).get("sample_size", 0),
                "updated_at": None, "is_default": True}
    return {"weights": doc["weights"], "sample_size": doc.get("sample_size", 0),
            "updated_at": doc.get("updated_at"), "is_default": False}


async def maybe_recompute(db) -> None:
    """Opportunistic trigger — call this after any event that changes the
    acceptance signal (a ✓ Verify, a newly-detected edit-diff). Internally
    throttled so hammering it (e.g. one call per verify click) costs almost
    nothing; the actual recompute only runs at most once an hour."""
    try:
        doc = await db.eval_weights.find_one({"id": _WEIGHTS_ID})
        last_checked = (doc or {}).get("checked_at")
        if last_checked:
            try:
                age = time.time() - datetime.fromisoformat(last_checked).timestamp()
                if age < RECOMPUTE_THROTTLE_SEC:
                    return
            except Exception:
                pass
        await _recompute(db, existing=doc)
    except Exception as exc:  # never let the meta-loop break a request
        logger.warning("eval_meta.maybe_recompute failed: %s", exc)


async def _recompute(db, existing: Optional[dict]) -> None:
    chains = await db.chains.find({"status": "complete"}).sort("completed_at", -1).limit(MAX_CHAINS_SCANNED).to_list(MAX_CHAINS_SCANNED)
    diff_rows = await db.edit_diffs.find({}).to_list(5000)
    corrected_chain_ids = {r.get("chain_id") for r in diff_rows if r.get("chain_id")}

    good = {"coverage": [], "rater": [], "novelty": []}
    bad = {"coverage": [], "rater": [], "novelty": []}
    build_count = 0
    for chain in chains:
        gens = chain.get("generations") or []
        if not gens:
            continue
        gen = gens[-1]
        if gen.get("composite_gated"):
            continue  # gated builds carry no useful signal about the weighted blend
        build_count += 1
        cov = (gen.get("coverage") or {}).get("coverage")
        nov = gen.get("novelty")
        rater = _rater_norm(gen.get("reward") or {})
        is_good = bool(chain.get("verified")) and chain["id"] not in corrected_chain_ids
        bucket = good if is_good else bad
        if cov is not None:
            bucket["coverage"].append(cov)
        bucket["rater"].append(rater)
        if nov is not None:
            bucket["novelty"].append(nov)

    now_iso = datetime.now(timezone.utc).isoformat()

    if build_count < MIN_SAMPLE_SIZE:
        # Not enough signal yet — record that we checked (for the throttle)
        # without touching the actual weights.
        await db.eval_weights.update_one(
            {"id": _WEIGHTS_ID},
            {"$set": {"id": _WEIGHTS_ID, "checked_at": now_iso,
                      "weights": (existing or {}).get("weights", DEFAULT_WEIGHTS),
                      "sample_size": build_count,
                      "updated_at": (existing or {}).get("updated_at")}},
            upsert=True,
        )
        return

    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    gaps = {dim: max(0.0, _mean(good[dim]) - _mean(bad[dim])) for dim in DEFAULT_WEIGHTS}
    total_gap = sum(gaps.values())
    if total_gap <= 0:
        computed = dict(DEFAULT_WEIGHTS)
    else:
        raw = {dim: gaps[dim] / total_gap for dim in DEFAULT_WEIGHTS}
        floored = {dim: max(0.1, raw[dim]) for dim in DEFAULT_WEIGHTS}  # no dimension ever hits 0
        ftotal = sum(floored.values())
        computed = {dim: floored[dim] / ftotal for dim in DEFAULT_WEIGHTS}

    prior = (existing or {}).get("weights", DEFAULT_WEIGHTS)
    blended_raw = {dim: 0.7 * prior.get(dim, DEFAULT_WEIGHTS[dim]) + 0.3 * computed[dim] for dim in DEFAULT_WEIGHTS}
    btotal = sum(blended_raw.values())
    blended = {dim: round(blended_raw[dim] / btotal, 3) for dim in DEFAULT_WEIGHTS}

    await db.eval_weights.update_one(
        {"id": _WEIGHTS_ID},
        {"$set": {"id": _WEIGHTS_ID, "weights": blended, "sample_size": build_count,
                  "good_count": len(good["rater"]),
                  "bad_count": len(bad["rater"]),
                  "updated_at": now_iso, "checked_at": now_iso}},
        upsert=True,
    )
    logger.info("eval_meta: recomputed weights %s from %d builds", blended, build_count)
