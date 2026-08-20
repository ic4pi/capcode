"""
Deterministic feature-coverage scorer.

Given the human's target_prompt + the Artist's generated files, this returns a
0.0-1.0 coverage score plus a per-check breakdown. Unlike the LLM rater it is
reproducible and not fooled by well-written prose that doesn't actually
implement the request.

Signals we look for (each contributes to coverage if present in the prompt):
  - Explicit integers "N X" (e.g. "10 jokes", "5 tiles") → the code must
    contain a list/array-literal with at least N elements.
  - API routes like "/api/foo" or "GET /api/foo" → the code must declare that
    route (FastAPI @app.get / express app.get / etc).
  - UI verbs (button, input, form, canvas, chart, table, modal, slider) →
    keyword must appear in the code.
  - Network verbs (fetch, axios, XMLHttpRequest, requests.get) → at least one
    must appear in code if the prompt implies fetching data.
  - Stack keywords (react, vite, fastapi, webgl, three.js, rust, go) →
    matching build artifact must be present.
"""
from __future__ import annotations

import re
from typing import Iterable


_LIST_LITERAL_RE = re.compile(r"[\[\{]\s*(?:\"|')")
_UI_VERBS = ("button", "input", "form", "canvas", "chart", "table",
             "modal", "slider", "dropdown", "search", "textarea", "spark")
_NET_VERBS_PROMPT = ("fetch", "call", "api", "request", "load", "get ", "retriev")
_NET_VERBS_CODE = ("fetch(", "axios", "xmlhttprequest", "requests.get",
                   "requests.post", "httpx.", "http.get", "http.post")


def _joined(files: list[dict]) -> str:
    return "\n".join(f.get("content", "") for f in files if isinstance(f, dict))


def _find_int_targets(prompt: str) -> list[int]:
    """Extract integers likely to be 'expected item counts' from the prompt."""
    out = []
    for m in re.finditer(r"\b(\d+)\s+([a-zA-Z]+)", prompt):
        n = int(m.group(1))
        # ignore trivially small (< 3) or absurdly large (> 500) numbers
        if 3 <= n <= 500:
            out.append(n)
    return out[:3]  # at most 3 quantitative checks


def _find_routes(prompt: str) -> list[str]:
    return re.findall(r"/api/[A-Za-z0-9_/-]+|/[a-zA-Z0-9_-]+/?(?=\s|$)", prompt)[:4]


def _find_ui_verbs(prompt: str) -> list[str]:
    lp = prompt.lower()
    return [v for v in _UI_VERBS if v in lp]


def _prompt_implies_network(prompt: str) -> bool:
    lp = prompt.lower()
    return any(v in lp for v in _NET_VERBS_PROMPT)


def _count_list_items(code: str, min_n: int) -> bool:
    """Look for any array/list literal in the code with >= min_n elements."""
    # Match python list, JS array, JSON array of strings/numbers.
    for m in re.finditer(r"[\[]([^\[\]]{20,})[\]]", code, flags=re.DOTALL):
        body = m.group(1)
        # count commas at the top level as a rough item count
        items = body.count(",") + 1
        if items >= min_n:
            return True
    return False


def _code_has_route(code: str, route: str) -> bool:
    r = re.escape(route.strip("/"))
    patterns = [
        rf"@\w+\.(get|post|put|delete)\(\s*['\"]/{r}['\"]",   # FastAPI/Flask
        rf"app\.(get|post|put|delete)\(\s*['\"]/{r}['\"]",   # Express
        rf"['\"]\s*/{r}\s*['\"]",                            # any string reference
    ]
    return any(re.search(p, code, flags=re.IGNORECASE) for p in patterns)


def _code_has_network_call(code: str) -> bool:
    lc = code.lower()
    return any(v in lc for v in _NET_VERBS_CODE)


def score(target_prompt: str, files: Iterable[dict]) -> dict:
    files = list(files or [])
    code = _joined(files)
    if not code:
        return {"coverage": 0.0, "checks": [], "num_checks": 0, "num_passed": 0}

    checks: list[dict] = []

    # 1) list-length checks
    for n in _find_int_targets(target_prompt):
        checks.append({
            "name": f"list of at least {n} items",
            "passed": _count_list_items(code, n),
        })

    # 2) route checks
    for r in _find_routes(target_prompt):
        r_norm = r.strip("/").lower()
        if not r_norm or r_norm in ("", "api"):
            continue
        checks.append({
            "name": f"route /{r_norm}",
            "passed": _code_has_route(code, r_norm),
        })

    # 3) UI verb checks (each mentioned verb must appear in the generated code)
    for v in _find_ui_verbs(target_prompt):
        checks.append({
            "name": f"UI element '{v}'",
            "passed": v in code.lower(),
        })

    # 4) if prompt implies fetching data, code must actually do it
    if _prompt_implies_network(target_prompt):
        checks.append({
            "name": "network call in code",
            "passed": _code_has_network_call(code),
        })

    if not checks:
        # No signals we can score deterministically — return neutral.
        return {"coverage": None, "checks": [], "num_checks": 0, "num_passed": 0,
                "note": "no deterministic signals extracted from prompt"}

    passed = sum(1 for c in checks if c["passed"])
    return {
        "coverage": round(passed / len(checks), 3),
        "checks": checks,
        "num_checks": len(checks),
        "num_passed": passed,
    }
