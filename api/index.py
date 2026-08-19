"""Vercel Python serverless entrypoint.

Vercel's @vercel/python runtime auto-discovers files under /api/ as serverless
functions. This single-file entry adds backend/ to sys.path and re-exports the
FastAPI `app` object so ALL /api/* traffic flows through the same FastAPI app
(with all its middleware, routers, and lifespan). No duplication of routes,
no per-endpoint files.
"""
import os
import sys

# Make backend/ importable — server.py + docdb.py + chain_generator.py etc.
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from server import app  # noqa: E402,F401  — Vercel discovers `app` here
