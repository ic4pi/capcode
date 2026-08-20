"""
edits.py — User Edit Diffing, the recursive loop's ground-truth signal.

Every generated build gets a `build_manifest` (sha256 per file) stamped on
completion. If the user pushes to GitHub and later commits changes, or
uploads their edited files back as a zip, we diff the new content against
the manifest and store the resulting hunks in `db.edit_diffs` — a permanent,
cross-build record of what CapCode's generator actually gets wrong.
`corrections_digest()` feeds the most recent hunks back into the Teacher's
prompt on every future build, closing the loop with no ML infra required.
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("edits")


def sha256_manifest(files: list[dict]) -> dict[str, str]:
    manifest = {}
    for f in files or []:
        path = (f.get("path") or "").strip()
        if not path:
            continue
        content = f.get("content") or ""
        manifest[path] = hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest()
    return manifest


def _normalize_upload_paths(uploaded: dict[str, str], manifest: dict[str, str]) -> dict[str, str]:
    """Zip tools (and our own /download endpoint) commonly nest everything
    under one top-level folder (e.g. `chain-abc123/index.html`). Try an exact
    match first; if a path doesn't hit the manifest, retry with the first
    path segment stripped so a re-zipped, re-uploaded project still lines up
    against the flat paths the manifest was built from."""
    out = {}
    for path, content in uploaded.items():
        if path in manifest:
            out[path] = content
            continue
        if "/" in path:
            stripped = path.split("/", 1)[1]
            if stripped in manifest:
                out[stripped] = content
                continue
        out[path] = content  # left as-is; check_upload_edits will just skip it
    return out


async def _store_diff(db, chain_id: str, file_path: str, diff_hunk: str,
                       built_at: Optional[str], source: str) -> None:
    """Append-only — every detected correction is kept (not overwritten) so
    corrections_digest() and the per-chain history reflect everything a user
    has ever fixed, not just the latest snapshot. Skips writing a duplicate
    when the most recent stored hunk for this file is byte-identical (so
    repeatedly clicking 'check for edits' with no new changes doesn't spam
    the history). Returns the stored record's seconds_since_build (or None
    if this call was a no-op dedup) so callers can echo it back immediately
    instead of the caller having to reload to see 'time since build'."""
    diff_hunk = diff_hunk[:4000]
    latest = await db.edit_diffs.find(
        {"chain_id": chain_id, "file_path": file_path}
    ).sort("detected_at", -1).limit(1).to_list(1)
    if latest and latest[0].get("diff_hunk") == diff_hunk:
        return latest[0].get("seconds_since_build")
    now = datetime.now(timezone.utc)
    seconds_since_build = None
    if built_at:
        try:
            seconds_since_build = int((now - datetime.fromisoformat(built_at)).total_seconds())
        except Exception:
            pass
    await db.edit_diffs.insert_one({
        "id": str(uuid.uuid4()), "chain_id": chain_id, "file_path": file_path,
        "diff_hunk": diff_hunk, "seconds_since_build": seconds_since_build,
        "source": source, "detected_at": now.isoformat(),
    })
    return seconds_since_build


async def check_git_edits(db, doc: dict, token: str) -> list[dict]:
    """Diffs the pushed commit against the repo's current HEAD via GitHub's
    compare API — no local clone needed, GitHub hands us a ready-made
    unified diff (`patch`) per changed file."""
    gh = doc.get("github") or {}
    repo = gh.get("repo")
    base_sha = gh.get("commit_sha")
    if not repo or not base_sha:
        return []
    manifest = doc.get("build_manifest") or {}
    changed = []
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.get(
            f"https://api.github.com/repos/{repo}/compare/{base_sha}...HEAD",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        if r.status_code != 200:
            logger.warning("github compare failed: %s %s", r.status_code, r.text[:200])
            return []
        data = r.json()
    for f in data.get("files", []):
        path = f.get("filename", "")
        patch = f.get("patch", "")
        if not path or not patch or path not in manifest:
            continue
        secs = await _store_diff(db, doc["id"], path, patch, doc.get("built_at"), "git")
        changed.append({"file_path": path, "diff_hunk": patch, "seconds_since_build": secs})
    return changed


async def check_upload_edits(db, doc: dict, uploaded: dict[str, str]) -> list[dict]:
    """`uploaded` is {path: content} extracted from a user-supplied zip.
    Diffed against the originally-generated content already stored on the
    chain doc — the sha256 manifest is just a cheap pre-filter so we only
    difflib the files that actually changed."""
    manifest = doc.get("build_manifest") or {}
    uploaded = _normalize_upload_paths(uploaded, manifest)
    gen = (doc.get("generations") or [{}])[-1]
    originals = {f.get("path"): f.get("content", "") for f in (gen.get("files") or [])}
    changed = []
    for path, new_content in uploaded.items():
        if path not in manifest:
            continue
        new_hash = hashlib.sha256(new_content.encode("utf-8", "ignore")).hexdigest()
        if new_hash == manifest[path]:
            continue
        before = originals.get(path, "")
        hunk = "\n".join(difflib.unified_diff(
            before.splitlines(), new_content.splitlines(),
            fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
        ))
        if not hunk:
            continue
        secs = await _store_diff(db, doc["id"], path, hunk, doc.get("built_at"), "upload")
        changed.append({"file_path": path, "diff_hunk": hunk, "seconds_since_build": secs})
    return changed


async def corrections_digest(db, limit: int = 5) -> str:
    """Cross-build learning signal injected into the Teacher's prompt — the
    N most recently-detected user corrections, regardless of whose chain
    they came from (same intentional cross-tenant sharing model already
    used for verified exemplars)."""
    rows = await db.edit_diffs.find({}).sort("detected_at", -1).limit(limit).to_list(limit)
    if not rows:
        return ""
    lines = []
    for r in rows:
        snippet = "\n".join((r.get("diff_hunk") or "").strip().splitlines()[:6])
        lines.append(f"- {r.get('file_path', '?')}: users previously corrected this pattern:\n{snippet}")
    return "\n\n".join(lines)
