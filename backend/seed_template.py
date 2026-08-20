"""
seed_template.py — renders a COMPLETE, RUNNABLE no-code code-builder as a folder.

Each gen produced by RECURSIVE.BBS is a working replica of RECURSIVE.BBS itself:
- a FastAPI backend with EVOLVE endpoint that calls NVIDIA NIM
- a single-page HTML frontend with the EVOLVE button + chain viewer + download
- its own seed_template.py copy so it can spawn the NEXT gen recursively
- run.sh, requirements.txt, README.md

The AI only designs the MUTATION between gens (name, hero copy, accent color,
scoring weights, philosophy). Everything else is the same proven template.
"""
from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Mutation defaults (the AI overrides these)
# ---------------------------------------------------------------------------
DEFAULT_MUTATION = {
    "name": "RecursiveBBS",
    "tagline": "an application that builds bot-builders",
    "philosophy": (
        "Each generation is a complete no-code code-builder, a real working "
        "FastAPI + single-page app that can itself spawn the next generation."
    ),
    "accent_hex": "#7cffb2",       # primary phosphor
    "accent2_hex": "#ff79c6",      # neon accent
    "weights": {
        "helpfulness": 0.35,
        "correctness": 0.30,
        "coherence": 0.20,
        "complexity": 0.10,
        "verbosity": -0.05,
    },
    "improvement_note": (
        "This generation behaves identically to its parent — no mutation was applied."
    ),
}


def render(mutation: dict | None = None, seed_template_src: str | None = None,
           stack: str | None = None) -> list[dict]:
    """Render a complete runnable folder for the requested stack.

    If the mutation dict already carries an Artist-authored ``files`` list, those
    files are used verbatim and this function only ADDS any missing scaffolding
    (run.sh, package.json/Cargo.toml/go.mod/requirements.txt, README.md).
    Otherwise a stack-appropriate boilerplate is rendered.
    """
    m = {**DEFAULT_MUTATION, **(mutation or {})}
    stack = (stack or m.get("stack") or "python-fastapi").lower().strip()
    name = (m.get("name") or "RecursiveBBS").strip()
    tagline = m.get("tagline") or DEFAULT_MUTATION["tagline"]
    philosophy = m.get("philosophy") or DEFAULT_MUTATION["philosophy"]
    accent = m.get("accent_hex") or DEFAULT_MUTATION["accent_hex"]
    accent2 = m.get("accent2_hex") or DEFAULT_MUTATION["accent2_hex"]
    improvement = m.get("improvement_note") or DEFAULT_MUTATION["improvement_note"]
    target = m.get("target_prompt") or m.get("target") or ""

    # ---- Artist-authored files: use verbatim, fill gaps only ------------
    artist_files = [f for f in (m.get("files") or []) if isinstance(f, dict)
                    and f.get("path") and isinstance(f.get("content"), str)]
    if artist_files:
        return _fill_gaps(artist_files, stack, name, tagline, philosophy, improvement,
                          accent, accent2, target)

    # ---- No artist files: fall back to stack boilerplate ----------------
    if "node" in stack or "vite" in stack or "react" in stack:
        return _render_node(name, tagline, philosophy, improvement, accent, accent2, target)
    if "rust" in stack:
        return _render_rust(name, tagline, philosophy, improvement, target)
    if stack == "go" or stack.startswith("go-"):
        return _render_go(name, tagline, philosophy, improvement, target)
    if "webgl" in stack or "html" in stack:
        return _render_webgl(name, tagline, philosophy, improvement, accent, accent2, target)

    # Default: python-fastapi
    weights = {**DEFAULT_MUTATION["weights"], **(m.get("weights") or {})}
    if seed_template_src is None:
        seed_template_src = Path(__file__).read_text()
    files = [
        {"path": "backend/server.py", "content": _SERVER_PY.format(
            name=name, tagline=_q(tagline), philosophy=_q(philosophy),
            improvement=_q(improvement),
            w_help=weights["helpfulness"], w_correct=weights["correctness"],
            w_cohere=weights["coherence"], w_complex=weights["complexity"],
            w_verbose=weights["verbosity"],
        )},
        {"path": "backend/seed_template.py", "content": seed_template_src},
        {"path": "backend/requirements.txt", "content":
            "fastapi==0.115.0\nuvicorn[standard]==0.30.6\npydantic==2.9.2\nopenai==1.99.9\n"},
        {"path": "frontend/index.html", "content": _INDEX_HTML.format(
            name=name, tagline=tagline.replace('"', "'"), accent=accent, accent2=accent2,
        )},
        {"path": "run.sh", "content": _RUN_SH},
        {"path": "README.md", "content": _README.format(
            name=name, tagline=tagline, philosophy=philosophy, improvement=improvement,
        )},
    ]
    return files


def _fill_gaps(artist_files, stack, name, tagline, philosophy, improvement,
               accent, accent2, target):
    """Take the Artist's files, add any missing scaffolding for the stack.
    Never overwrites what the Artist wrote."""
    have = {f["path"] for f in artist_files}
    out = list(artist_files)

    def add_if_missing(path, content):
        if path not in have:
            out.append({"path": path, "content": content})

    # README is always nice.
    add_if_missing("README.md", f"# {name}\n\n> {tagline}\n\n{philosophy}\n\n**Target:** {target}\n\n{improvement}\n\n## Run\n\n```bash\nbash run.sh\n```\n")

    if "node" in stack or "vite" in stack or "react" in stack:
        if "package.json" not in have:
            add_if_missing("package.json", json.dumps({
                "name": _slug(name), "version": "0.1.0", "type": "module",
                "scripts": {"dev": "vite --host --port ${APP_PORT:-8000}",
                            "build": "vite build", "preview": "vite preview --port ${APP_PORT:-8000} --host"},
                "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
                "devDependencies": {"vite": "^5.4.0", "@vitejs/plugin-react": "^4.3.1"},
            }, indent=2))
        add_if_missing("vite.config.js",
            "import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\n"
            "export default defineConfig({ plugins:[react()], server:{ host:true, port: Number(process.env.APP_PORT)||8000 }});\n")
        add_if_missing("run.sh",
            '#!/usr/bin/env bash\nset -e\ncd "$(dirname "$0")"\n'
            'npm install --silent >/dev/null 2>&1 || yarn install --silent\n'
            'exec npx vite --host --port ${APP_PORT:-8000}\n')
    elif "rust" in stack:
        if "Cargo.toml" not in have:
            add_if_missing("Cargo.toml", f'[package]\nname = "{_slug(name)}"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\ntiny_http = "0.12"\n')
        add_if_missing("run.sh", '#!/usr/bin/env bash\nset -e\ncd "$(dirname "$0")"\ncargo run --release\n')
    elif stack == "go" or stack.startswith("go-"):
        add_if_missing("go.mod", f"module {_slug(name)}\n\ngo 1.22\n")
        add_if_missing("run.sh", '#!/usr/bin/env bash\nset -e\ncd "$(dirname "$0")"\nexec go run main.go\n')
    elif "webgl" in stack or "html" in stack:
        add_if_missing("run.sh", '#!/usr/bin/env bash\nset -e\ncd "$(dirname "$0")"\nexec python3 -m http.server ${APP_PORT:-8000}\n')
    else:
        # python-fastapi default
        add_if_missing("backend/requirements.txt",
            "fastapi==0.115.0\nuvicorn[standard]==0.30.6\npydantic==2.9.2\nhttpx==0.27.2\n")
        add_if_missing("run.sh",
            '#!/usr/bin/env bash\nset -e\ncd "$(dirname "$0")"\n'
            'python3 -m venv .venv 2>/dev/null || true\n'
            'source .venv/bin/activate\n'
            'pip install -q -r backend/requirements.txt\n'
            'cd backend\nexec uvicorn server:app --host 0.0.0.0 --port ${APP_PORT:-8000}\n')
    return out


# ---------------------------------------------------------------------------
# Multi-stack minimal renderers (node/vite, rust, go, webgl)
# ---------------------------------------------------------------------------
def _slug(s: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9-]+", "-", (s or "app").lower()).strip("-") or "app"


def _render_node(name, tagline, philosophy, improvement, accent, accent2, target):
    slug = _slug(name)
    pkg = {
        "name": slug, "version": "0.1.0", "type": "module",
        "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview --port 8000 --host"},
        "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
        "devDependencies": {"vite": "^5.4.0", "@vitejs/plugin-react": "^4.3.1"},
    }
    index_html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{name}</title>
<style>body{{background:#070907;color:{accent};font:14px ui-monospace,monospace;padding:24px;margin:0}}
h1{{color:{accent};text-shadow:0 0 6px {accent}}} .tag{{color:{accent2}}}</style></head>
<body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body></html>
"""
    main_jsx = f"""import React from "react"; import ReactDOM from "react-dom/client";
function App(){{ return (<div>
<h1>{name}</h1><p className="tag">{tagline}</p>
<p style={{{{maxWidth:640}}}}>{philosophy}</p>
<p><b>Target:</b> {target}</p><p><b>Delta:</b> {improvement}</p>
</div>); }}
ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
"""
    vite_cfg = """import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins:[react()], server:{ host:true, port: Number(process.env.APP_PORT)||8000 }});
"""
    run_sh = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
npm install --silent >/dev/null 2>&1 || yarn install --silent
exec npx vite --host --port ${APP_PORT:-8000}
"""
    readme = f"# {name}\n\n> {tagline}\n\n{philosophy}\n\n## Run\n\n```bash\nbash run.sh\n```\n"
    return [
        {"path": "package.json", "content": json.dumps(pkg, indent=2)},
        {"path": "index.html", "content": index_html},
        {"path": "src/main.jsx", "content": main_jsx},
        {"path": "vite.config.js", "content": vite_cfg},
        {"path": "run.sh", "content": run_sh},
        {"path": "README.md", "content": readme},
    ]


def _render_rust(name, tagline, philosophy, improvement, target):
    slug = _slug(name)
    cargo = f"""[package]
name = "{slug}"
version = "0.1.0"
edition = "2021"

[dependencies]
tiny_http = "0.12"
"""
    main_rs = f"""use tiny_http::{{Server, Response}};
fn main() {{
    let port = std::env::var("APP_PORT").unwrap_or_else(|_| "8000".to_string());
    let addr = format!("0.0.0.0:{{}}", port);
    let server = Server::http(&addr).unwrap();
    println!("{name} listening on {{}}", addr);
    for req in server.incoming_requests() {{
        let body = format!("<h1>{name}</h1><p>{tagline}</p><p>{philosophy}</p><p>target: {target}</p>");
        let _ = req.respond(Response::from_string(body).with_header(
            tiny_http::Header::from_bytes(&b"Content-Type"[..], &b"text/html; charset=utf-8"[..]).unwrap()));
    }}
}}
"""
    run_sh = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
cargo run --release
"""
    readme = f"# {name}\n\n> {tagline}\n\n{philosophy}\n\n{improvement}\n"
    return [
        {"path": "Cargo.toml", "content": cargo},
        {"path": "src/main.rs", "content": main_rs},
        {"path": "run.sh", "content": run_sh},
        {"path": "README.md", "content": readme},
    ]


def _render_go(name, tagline, philosophy, improvement, target):
    slug = _slug(name)
    gomod = f"module {slug}\n\ngo 1.22\n"
    main_go = f"""package main

import (
    "fmt"
    "net/http"
    "os"
)

func main() {{
    port := os.Getenv("APP_PORT")
    if port == "" {{ port = "8000" }}
    http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {{
        w.Header().Set("Content-Type", "text/html; charset=utf-8")
        fmt.Fprintf(w, "<h1>{name}</h1><p>{tagline}</p><p>{philosophy}</p><p>target: {target}</p>")
    }})
    fmt.Println("{name} listening on :" + port)
    http.ListenAndServe(":"+port, nil)
}}
"""
    run_sh = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
exec go run main.go
"""
    readme = f"# {name}\n\n> {tagline}\n\n{philosophy}\n\n{improvement}\n"
    return [
        {"path": "go.mod", "content": gomod},
        {"path": "main.go", "content": main_go},
        {"path": "run.sh", "content": run_sh},
        {"path": "README.md", "content": readme},
    ]


def _render_webgl(name, tagline, philosophy, improvement, accent, accent2, target):
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{name}</title>
<style>html,body{{margin:0;height:100%;background:#000;color:{accent};font:14px ui-monospace,monospace}}
canvas{{display:block;width:100vw;height:100vh}} .overlay{{position:fixed;top:10px;left:10px}}
h1{{margin:0;text-shadow:0 0 6px {accent}}} .tag{{color:{accent2}}}</style></head>
<body>
<div class="overlay"><h1>{name}</h1><div class="tag">{tagline}</div>
<div>target: {target}</div><div>philosophy: {philosophy}</div></div>
<canvas id="c"></canvas>
<script>
const c=document.getElementById("c"); c.width=innerWidth; c.height=innerHeight;
const gl=c.getContext("webgl"); gl.clearColor(0.02,0.05,0.02,1);
function loop(t){{ gl.clearColor(0.02+0.02*Math.sin(t/900),0.05,0.02,1); gl.clear(gl.COLOR_BUFFER_BIT); requestAnimationFrame(loop); }}
requestAnimationFrame(loop);
</script></body></html>
"""
    run_sh = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
exec python3 -m http.server ${APP_PORT:-8000}
"""
    readme = f"# {name}\n\n> {tagline}\n\n{philosophy}\n\n{improvement}\n"
    return [
        {"path": "index.html", "content": html},
        {"path": "run.sh", "content": run_sh},
        {"path": "README.md", "content": readme},
    ]



def _q(s: str) -> str:
    """Escape for inclusion in a Python triple-quoted string."""
    return (s or "").replace('"""', '\\"\\"\\"')


# ---------------------------------------------------------------------------
# Static template strings (literal sources of the generated app)
# ---------------------------------------------------------------------------

_RUN_SH = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r backend/requirements.txt
cd backend
exec uvicorn server:app --host 0.0.0.0 --port ${APP_PORT:-8000}
"""


_README = """# {name}

> {tagline}

This is a complete, runnable no-code code-builder generated by RECURSIVE.BBS.

## Philosophy

{philosophy}

## What's new in this generation

{improvement}

## Run

```bash
export NVIDIA_API_KEY=nvapi-…   # optional; STUB_MODE works without one
bash run.sh
```

Open http://localhost:8000 and push EVOLVE to produce the next generation.

## What you get

- `backend/server.py` — FastAPI app with the EVOLVE endpoint
- `backend/seed_template.py` — the template renderer (carried with every gen)
- `frontend/index.html` — single-page UI, no build step
- `run.sh` — venv + pip + uvicorn

Each EVOLVE press calls NVIDIA NIM (GLM-5.1) to design a mutation, then
synthesizes a complete folder of working code for the next generation.
You download the result as a `.zip`.
"""


# NOTE: server.py uses str.format() — all literal { and } in the SOURCE template
# must be doubled ({{ and }}).
_SERVER_PY = r'''"""
{name} — {tagline}
Generated by RECURSIVE.BBS.

Improvement vector (from parent gen): {improvement}
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import random
import re
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Make seed_template importable
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import seed_template

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("{name}")

NAME = "{name}"
TAGLINE = """{tagline}"""
PHILOSOPHY = """{philosophy}"""

# Composite-score weights (the AI may mutate these per gen)
W_HELP = {w_help}
W_CORRECT = {w_correct}
W_COHERE = {w_cohere}
W_COMPLEX = {w_complex}
W_VERBOSE = {w_verbose}

# ---- NIM client ------------------------------------------------------------
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "").strip()
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
GEN_MODEL = "z-ai/glm-5.1"
CRITIC_MODEL = "minimaxai/minimax-m2.7"
RATER_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"

_client = None
NIM_ENABLED = False
if NVIDIA_API_KEY:
    try:
        from openai import AsyncOpenAI
        _client = AsyncOpenAI(api_key=NVIDIA_API_KEY, base_url=NIM_BASE_URL, timeout=90, max_retries=0)
        NIM_ENABLED = True
    except Exception as exc:
        log.warning("NIM init failed: %s", exc)


async def _chat(model: str, messages: list[dict], **kwargs) -> Optional[str]:
    if not NIM_ENABLED or _client is None:
        return None
    try:
        r = await _client.chat.completions.create(model=model, messages=messages, **kwargs)
        msg = r.choices[0].message
        out = (msg.content or "").strip()
        if not out:
            reasoning = getattr(msg, "reasoning_content", "") or ""
            paras = [p.strip() for p in reasoning.split("\n\n") if p.strip()]
            out = paras[-1] if paras else reasoning.strip()
        return out
    except Exception as exc:
        log.error("NIM %s call failed: %s", model, exc)
        return None


def _extract_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {{}}
    m = re.search(r"```(?:json)?\s*(\{{[\s\S]*?\}})\s*```", raw)
    cand = m.group(1) if m else None
    if cand is None:
        start = raw.find("{{")
        if start < 0:
            return {{}}
        depth = 0
        end = -1
        for i, c in enumerate(raw[start:], start=start):
            if c == "{{":
                depth += 1
            elif c == "}}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        cand = raw[start:end] if end > 0 else None
    if not cand:
        return {{}}
    try:
        return json.loads(cand)
    except Exception:
        try:
            return json.loads(re.sub(r",\s*([}}\]])", r"\1", cand))
        except Exception:
            return {{}}


SYSTEM_DESIGN = """You are {{name}}, an evolution engine. You design the next generation of a no-code code-builder application. The next gen MUST be a complete working FastAPI + single-page app that itself can evolve further.

Output STRICT JSON only. No prose. No code fences. No trailing commas. Be CREATIVE about mutations:
{{{{
  "name": "PascalCase short name, distinct from parents",
  "tagline": "one short line",
  "philosophy": "2 sentences. What this gen improves over its parent.",
  "improvement_note": "the concrete mutation (e.g. 'new accent color and tighter scoring rubric', 'added a second critic pass', 'inverted the verbosity penalty', 'auto-injects unit-test scaffold')",
  "accent_hex": "#rrggbb primary phosphor color",
  "accent2_hex": "#rrggbb secondary neon color",
  "weights": {{{{
    "helpfulness": 0.30-0.45,
    "correctness": 0.25-0.40,
    "coherence":   0.15-0.25,
    "complexity":  0.05-0.15,
    "verbosity":  -0.10-0.00
  }}}}
}}}}
""".replace("{{name}}", NAME)


# ---- in-memory chain store -------------------------------------------------
CHAINS: dict[str, dict] = {{}}


# ---- the evolve loop -------------------------------------------------------
async def design_mutation(history: list[dict]) -> dict:
    user_msg = "Chain so far:\n" + ("\n".join(
        f"- Gen {{g['gen']}}: {{g.get('name','?')}} — {{g.get('improvement_note','')}}"
        for g in history
    ) or "(none — design the first descendant)")
    raw = await _chat(
        GEN_MODEL,
        [{{"role": "system", "content": SYSTEM_DESIGN}},
         {{"role": "user", "content": user_msg + "\nDesign the next gen as strict JSON."}}],
        temperature=0.85, max_tokens=800,
    )
    parsed = _extract_json(raw) if raw else {{}}
    if not parsed.get("name"):
        # deterministic mutation fallback (no AI required)
        rng = random.Random(len(history))
        palettes = [
            ("#7cffb2", "#ff79c6"), ("#39ffea", "#ffea00"),
            ("#ff8c66", "#bbf7d0"), ("#c084fc", "#fde047"),
        ]
        a1, a2 = palettes[rng.randint(0, len(palettes) - 1)]
        parsed = {{
            "name": f"Gen{{len(history)+2}}Replica",
            "tagline": "a mutated replica",
            "philosophy": "Auto-mutated descendant — NIM was unreachable, so a deterministic mutation was applied.",
            "improvement_note": "shifted accent palette and rebalanced scoring weights",
            "accent_hex": a1,
            "accent2_hex": a2,
            "weights": {{
                "helpfulness": round(0.35 + rng.uniform(-0.05, 0.05), 2),
                "correctness": round(0.30 + rng.uniform(-0.05, 0.05), 2),
                "coherence":   round(0.20 + rng.uniform(-0.05, 0.05), 2),
                "complexity":  round(0.10 + rng.uniform(-0.03, 0.05), 2),
                "verbosity":  round(-0.05 + rng.uniform(-0.05, 0.05), 2),
            }},
        }}
    return parsed


async def critique_gen(gen: dict) -> Optional[str]:
    brief = json.dumps({{k: gen[k] for k in ("name","philosophy","improvement_note","weights") if k in gen}}, indent=2)
    return await _chat(
        CRITIC_MODEL,
        [{{"role": "system", "content": "Review this code-builder mutation in 2-3 specific sentences. No preamble. No markdown. Under 90 words."}},
         {{"role": "user", "content": brief}}],
        temperature=0.5, max_tokens=1200,
    )


async def score_gen(gen: dict) -> dict:
    rubric = ('Score on 5 dims (each 0.0-4.0). Output ONLY this JSON: '
              '{{{{"helpfulness":0,"correctness":0,"coherence":0,"complexity":0,"verbosity":0}}}}')
    raw = await _chat(
        RATER_MODEL,
        [{{"role": "system", "content": rubric}},
         {{"role": "user", "content": json.dumps(gen, indent=2)}}],
        temperature=0.1, max_tokens=3000,
    )
    data = _extract_json(raw) if raw else {{}}
    keys = ("helpfulness", "correctness", "coherence", "complexity", "verbosity")
    if not any(k in data for k in keys):
        # deterministic fallback
        rng = random.Random(hash(gen.get("name","")) & 0xffff)
        return {{k: round(rng.uniform(1.5, 3.5), 2) for k in keys}}
    return {{k: float(data.get(k, 0.0)) for k in keys}}


def composite(scores: dict) -> float:
    return round(
        W_HELP * scores.get("helpfulness", 0)
        + W_CORRECT * scores.get("correctness", 0)
        + W_COHERE * scores.get("coherence", 0)
        + W_COMPLEX * scores.get("complexity", 0)
        + W_VERBOSE * abs(scores.get("verbosity", 2) - 2.0),
        3,
    )


async def run_chain(chain_id: str, depth: int) -> None:
    chain = CHAINS[chain_id]
    history: list[dict] = []
    try:
        for i in range(depth):
            mutation = await design_mutation(history)
            mutation["gen"] = i + 2
            files = seed_template.render(
                mutation, seed_template_src=Path(__file__).parent.joinpath("seed_template.py").read_text()
            )
            mutation["files"] = files
            critic, scores = await asyncio.gather(critique_gen(mutation), score_gen(mutation))
            mutation["critic_notes"] = critic or "(critic unavailable)"
            mutation["reward"] = scores
            mutation["composite_score"] = composite(scores)
            chain["generations"].append(mutation)
            history.append({{
                "gen": mutation["gen"], "name": mutation.get("name"),
                "improvement_note": mutation.get("improvement_note", ""),
            }})
        chain["status"] = "complete"
        chain["completed_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        log.exception("chain %s failed", chain_id)
        chain["status"] = "failed"
        chain["error"] = str(exc)


# ---- FastAPI ---------------------------------------------------------------
app = FastAPI(title=NAME)
FRONTEND = HERE.parent / "frontend"


class EvolveReq(BaseModel):
    depth: int = 3


@app.get("/api/info")
def info():
    return {{
        "name": NAME, "tagline": TAGLINE, "philosophy": PHILOSOPHY,
        "nim_enabled": NIM_ENABLED, "total_chains": len(CHAINS),
        "weights": {{"helpfulness": W_HELP, "correctness": W_CORRECT,
                     "coherence": W_COHERE, "complexity": W_COMPLEX, "verbosity": W_VERBOSE}},
    }}


@app.post("/api/evolve")
async def evolve(req: EvolveReq, background_tasks: BackgroundTasks):
    depth = max(1, min(5, int(req.depth)))
    chain_id = str(uuid.uuid4())
    CHAINS[chain_id] = {{
        "id": chain_id, "depth": depth, "status": "running",
        "generations": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }}
    background_tasks.add_task(run_chain, chain_id, depth)
    return CHAINS[chain_id]


@app.get("/api/chains/{{chain_id}}")
def get_chain(chain_id: str):
    if chain_id not in CHAINS:
        raise HTTPException(404, "chain not found")
    return CHAINS[chain_id]


@app.get("/api/chains")
def list_chains():
    return [
        {{**{{k: v for k, v in c.items() if k != "generations"}},
          "generations": [{{kk: g[kk] for kk in g if kk != "files"}} for g in c["generations"]]}}
        for c in CHAINS.values()
    ]


@app.get("/api/chains/{{chain_id}}/download")
def download_chain(chain_id: str):
    c = CHAINS.get(chain_id)
    if not c:
        raise HTTPException(404, "chain not found")
    if c["status"] != "complete":
        raise HTTPException(409, f"chain status is {{c['status']}}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for g in c["generations"]:
            folder = f"gen-{{g['gen']:02d}}-{{re.sub(r'[^A-Za-z0-9]+', '-', g.get('name','gen'))}}"
            for f in g.get("files", []):
                zf.writestr(f"{{folder}}/{{f['path']}}", f["content"])
            zf.writestr(f"{{folder}}/.manifest.json", json.dumps(
                {{k: g[k] for k in g if k != "files"}}, indent=2))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
        headers={{"Content-Disposition": f'attachment; filename="chain-{{chain_id[:8]}}.zip"'}})


@app.get("/api/chains/{{chain_id}}/download/{{gen}}")
def download_gen(chain_id: str, gen: int):
    c = CHAINS.get(chain_id)
    if not c:
        raise HTTPException(404, "chain not found")
    g = next((x for x in c["generations"] if x.get("gen") == gen), None)
    if not g:
        raise HTTPException(404, f"gen {{gen}} not in chain")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        folder = re.sub(r'[^A-Za-z0-9]+', '-', g.get("name", "gen"))
        for f in g.get("files", []):
            zf.writestr(f"{{folder}}/{{f['path']}}", f["content"])
        zf.writestr(f"{{folder}}/.manifest.json", json.dumps(
            {{k: g[k] for k in g if k != "files"}}, indent=2))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
        headers={{"Content-Disposition": f'attachment; filename="gen-{{gen}}.zip"'}})


if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "index.html")
'''


# NOTE: index.html uses str.format() — literal { and } in CSS/JS must be doubled.
_INDEX_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{name}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {{
    --bg:#070907; --fg:{accent}; --mut:#5a5a5a; --line:#1e211e;
    --acc:{accent2}; --warn:#ffea00;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--fg);font:14px ui-monospace,SFMono-Regular,Menlo,monospace;padding:20px;max-width:1200px;margin:0 auto;min-height:100vh}}
  header{{border:1px solid var(--line);padding:20px;margin-bottom:18px}}
  h1{{margin:0;font-size:28px;letter-spacing:.05em;text-transform:uppercase;text-shadow:0 0 6px var(--fg)}}
  .tagline{{color:var(--mut);margin-top:6px;font-size:13px}}
  .pill{{display:inline-block;padding:3px 10px;border:1px solid var(--acc);color:var(--acc);font-size:11px;letter-spacing:.1em;text-transform:uppercase;margin-right:8px}}
  .pill.ok{{border-color:var(--fg);color:var(--fg)}}
  .panel{{border:1px solid var(--line);padding:18px;margin-bottom:18px;background:rgba(255,255,255,0.01)}}
  button{{background:transparent;color:var(--fg);border:1px solid var(--fg);padding:10px 18px;cursor:pointer;font:inherit;text-transform:uppercase;letter-spacing:.1em}}
  button:hover{{background:var(--fg);color:#000}}
  button:disabled{{opacity:.35;cursor:not-allowed}}
  .evolve-btn{{padding:24px 36px;font-size:18px;border-width:2px}}
  .row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0}}
  .grid{{display:grid;gap:12px}}
  .gen{{border:1px solid var(--acc);padding:14px;margin-bottom:12px}}
  .gen h3{{margin:0;color:var(--fg);text-transform:uppercase;letter-spacing:.05em}}
  .muted{{color:var(--mut);font-size:12px}}
  .bar{{display:flex;justify-content:space-between;gap:8px;font-size:11px;border-bottom:1px dashed var(--line);padding:2px 0}}
  pre{{background:#000;padding:12px;border:1px solid var(--line);white-space:pre-wrap;overflow:auto;max-height:240px;font-size:11px}}
  a.btn{{display:inline-block;padding:6px 12px;border:1px solid var(--fg);color:var(--fg);text-decoration:none;text-transform:uppercase;letter-spacing:.1em;font-size:12px}}
  a.btn:hover{{background:var(--fg);color:#000}}
  .tabs{{display:flex;gap:0;border-bottom:1px solid var(--line);margin-bottom:8px}}
  .tab{{padding:6px 10px;cursor:pointer;border:1px solid var(--line);border-bottom:none;font-size:11px;background:transparent;color:var(--mut)}}
  .tab.active{{color:var(--fg);border-color:var(--fg)}}
</style>
</head>
<body>

<header>
  <h1>{name}</h1>
  <div class="tagline">{tagline}</div>
  <div class="row" style="margin-top:14px">
    <span class="pill ok" id="status">booting…</span>
    <span class="pill">no input. one button.</span>
    <span class="pill">AI -&gt; AI handoff</span>
  </div>
</header>

<section class="panel">
  <div class="row" style="justify-content:space-between">
    <div>
      <h2 style="margin:0;font-size:14px;letter-spacing:.1em;text-transform:uppercase;color:var(--acc)">[ autonomous evolution ]</h2>
      <div class="muted" style="margin-top:6px;max-width:680px">
        push the button. AI designs the next generation as a working code-builder, exactly like this one but mutated.
        the AI hands off again to design the gen after that. you download the whole chain.
      </div>
    </div>
    <button class="evolve-btn" id="evolve">EVOLVE &gt;</button>
  </div>
  <div class="row" style="margin-top:14px">
    <span class="muted">depth:</span>
    <select id="depth" style="background:#000;color:var(--fg);border:1px solid var(--line);padding:6px;font:inherit">
      <option value="2">2</option><option value="3" selected>3</option>
      <option value="4">4</option><option value="5">5</option>
    </select>
    <span class="muted" id="progress"></span>
  </div>
</section>

<section id="chain-section"></section>

<script>
const $ = (id) => document.getElementById(id);
async function loadInfo() {{
  const r = await fetch('/api/info'); const d = await r.json();
  $('status').textContent = d.nim_enabled ? 'NVIDIA NIM :: LIVE' : 'STUB MODE (no key)';
}}

function renderChain(c) {{
  const root = $('chain-section'); root.innerHTML = '';
  if (!c) return;
  const head = document.createElement('div'); head.className = 'panel';
  head.innerHTML = `
    <div class="row" style="justify-content:space-between">
      <div>
        <h2 style="margin:0;font-size:14px;letter-spacing:.1em;text-transform:uppercase">[ chain :: ${{c.id.slice(0,12)}} ]</h2>
        <div class="muted" style="margin-top:4px">status: <strong>${{c.status}}</strong> | ${{c.generations.length}}/${{c.depth}} gens</div>
      </div>
      ${{c.status==='complete'?`<a class="btn" href="/api/chains/${{c.id}}/download">download chain.zip</a>`:''}}
    </div>`;
  root.appendChild(head);

  for (const g of c.generations) {{
    const el = document.createElement('div'); el.className='gen';
    const files = g.files || [];
    const r = g.reward || {{}};
    const bar = (k, v, max=4) => `<div class="bar"><span class="muted">${{k.toUpperCase()}}</span><span>${{Number(v||0).toFixed(2)}}</span></div>`;
    el.innerHTML = `
      <div class="row" style="justify-content:space-between">
        <div>
          <h3>Gen ${{g.gen}} :: ${{g.name||'?'}}</h3>
          <div class="muted" style="margin-top:2px">${{g.tagline||''}}</div>
        </div>
        <a class="btn" href="/api/chains/${{c.id}}/download/${{g.gen}}">gen-${{g.gen}}.zip</a>
      </div>
      <div class="muted" style="margin:8px 0">
        <strong>improvement:</strong> ${{g.improvement_note||''}}<br>
        <strong>philosophy:</strong> ${{g.philosophy||''}}
      </div>
      <div class="grid" style="grid-template-columns:1fr 1fr">
        <div>
          <div class="muted" style="margin-bottom:4px">CRITIC</div>
          <pre>${{(g.critic_notes||'').replace(/</g,'&lt;')}}</pre>
        </div>
        <div>
          <div class="muted" style="margin-bottom:4px">SCORECARD</div>
          ${{bar('help', r.helpfulness)}}${{bar('correct', r.correctness)}}${{bar('cohere', r.coherence)}}
          ${{bar('complex', r.complexity)}}${{bar('verbose', r.verbosity)}}
          <div class="bar" style="border-top:1px solid var(--fg);margin-top:4px"><strong>COMPOSITE</strong><strong>${{Number(g.composite_score||0).toFixed(3)}}</strong></div>
        </div>
      </div>
      ${{files.length ? `<div class="muted" style="margin-top:10px">files:</div>
      <div class="tabs">${{files.map((f,i)=>`<button class="tab ${{i===0?'active':''}}" data-i="${{i}}">${{f.path}}</button>`).join('')}}</div>
      <pre class="file-view">${{(files[0]?.content||'').replace(/</g,'&lt;')}}</pre>`:''}}
    `;
    root.appendChild(el);
    if (files.length) {{
      const tabs = el.querySelectorAll('.tab');
      const view = el.querySelector('.file-view');
      tabs.forEach(t => t.onclick = () => {{
        tabs.forEach(x=>x.classList.remove('active'));
        t.classList.add('active');
        view.textContent = files[+t.dataset.i].content;
      }});
    }}
  }}
}}

let pollId = null;
$('evolve').onclick = async () => {{
  const depth = +$('depth').value;
  $('evolve').disabled = true; $('progress').textContent = 'kicking off…';
  try {{
    const r = await fetch('/api/evolve', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{depth}})}});
    const stub = await r.json();
    if (pollId) clearInterval(pollId);
    pollId = setInterval(async () => {{
      const rr = await fetch('/api/chains/'+stub.id);
      const c = await rr.json();
      $('progress').textContent = `gen ${{c.generations.length}}/${{c.depth}} | ${{c.status}}`;
      renderChain(c);
      if (c.status === 'complete' || c.status === 'failed') {{
        clearInterval(pollId); pollId = null;
        $('evolve').disabled = false;
      }}
    }}, 3000);
  }} catch (e) {{
    console.error(e); $('progress').textContent = 'error: ' + e.message;
    $('evolve').disabled = false;
  }}
}};

loadInfo();
</script>
</body>
</html>
'''
