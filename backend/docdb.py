"""
docdb.py — a tiny Mongo-look-alike adapter over Neon Postgres (asyncpg + JSONB).

Goal: keep server.py's ~15 call sites (`db.chains.find_one`, `db.settings.update_one`,
`db.chains.find({..}).sort(..).limit(..).to_list(..)`, etc.) working without a
rewrite while the underlying store moves from Mongo to Postgres.

One physical table, `docs (collection TEXT, id TEXT, data JSONB, ...)`, keyed by
`(collection, id)`. `id` is extracted from the document at insert/upsert time
using the per-collection id field (see `COLLECTION_ID_FIELDS`). Equality queries
translate to `data @> $jsonb`. `$set` and `$push` operators are supported.
Sorting uses `data->>'field'` casting to timestamptz when the values look ISO.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Optional

import asyncpg

logger = logging.getLogger("docdb")

# Which JSON field to use as the primary key per collection.
COLLECTION_ID_FIELDS: dict[str, str] = {
    "chains": "id",
    "settings": "session_id",
    "users": "user_id",
    "user_sessions": "session_token",
    "venice_consent": "consent_id",
    "usage_ledger": "id",
    "chain_progress": "id",
    "knowledge_modules": "id",
    "billing_cycles": "user_id",
}

_pool: Optional[asyncpg.Pool] = None


async def connect(url: str) -> None:
    """Open the asyncpg pool and ensure the docs table + indexes exist."""
    global _pool
    if _pool is not None:
        return
    # statement_cache_size=0 keeps us compatible with PgBouncer-style poolers
    # that Neon uses; each query re-prepares which is fine for our low QPS.
    _pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=8,
        command_timeout=30,
        statement_cache_size=0,
    )
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS docs (
                collection TEXT NOT NULL,
                id         TEXT NOT NULL,
                data       JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (collection, id)
            );
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS docs_data_gin ON docs USING GIN (data jsonb_path_ops);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS docs_coll_created ON docs (collection, (data->>'created_at') DESC);"
        )
    logger.info("docdb connected to Postgres")


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _pool_or_raise() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("docdb.connect() has not been called yet")
    return _pool


# ---------------------------------------------------------------------------
# Query translation
# ---------------------------------------------------------------------------
_NULL_QUERY = "FALSE"


def _query_to_sql(collection: str, query: dict) -> tuple[str, list]:
    """Turn a Mongo-style equality query into a WHERE clause + params.

    Supported:
      - {} → all docs in collection
      - {"field": value, ...} → data @> '{"field": value, ...}'
      - {"_id": "__no_owner__"} → match nothing (owner sentinel)
    """
    if not query:
        return "collection = $1", [collection]

    # Owner sentinel — match nothing.
    if query.get("_id") == "__no_owner__":
        return _NULL_QUERY, []

    # Strip Mongo-internal _id (we don't store it) and reserved keys.
    clean = {k: v for k, v in query.items() if k != "_id" and not k.startswith("$")}
    if not clean:
        return "collection = $1", [collection]

    return (
        "collection = $1 AND data @> $2::jsonb",
        [collection, json.dumps(clean, default=str)],
    )


def _extract_id(collection: str, doc: dict) -> str:
    """Get the document's primary-key value based on the collection's id field."""
    field = COLLECTION_ID_FIELDS.get(collection, "id")
    val = doc.get(field)
    if not val:
        # Fall back to a random id so callers that don't provide one still work.
        val = str(uuid.uuid4())
        doc[field] = val
    return str(val)


# ---------------------------------------------------------------------------
# Cursor — supports .sort().limit().to_list()
# ---------------------------------------------------------------------------
class _Cursor:
    def __init__(self, collection: str, query: dict):
        self._coll = collection
        self._query = query
        self._sort: list[tuple[str, int]] = []
        self._limit: Optional[int] = None

    def sort(self, field: str | list, direction: int = 1) -> "_Cursor":
        if isinstance(field, list):
            for f, d in field:
                self._sort.append((f, d))
        else:
            self._sort.append((field, direction))
        return self

    def limit(self, n: int) -> "_Cursor":
        self._limit = int(n)
        return self

    async def to_list(self, length: Optional[int] = None) -> list[dict]:
        n = self._limit if self._limit is not None else length
        where, params = _query_to_sql(self._coll, self._query)
        sql = f"SELECT data FROM docs WHERE {where}"

        # Sort translation — cast the JSON text to timestamptz for datetime-ish
        # fields (created_at, verified_at, completed_at), otherwise sort as text.
        if self._sort:
            parts = []
            for f, d in self._sort:
                direction = "DESC" if d == -1 else "ASC"
                if f.endswith("_at") or f in ("created_at",):
                    parts.append(f"(data->>'{f}')::timestamptz {direction} NULLS LAST")
                else:
                    parts.append(f"data->>'{f}' {direction} NULLS LAST")
            sql += " ORDER BY " + ", ".join(parts)

        if n is not None:
            sql += f" LIMIT {int(n)}"

        async with _pool_or_raise().acquire() as conn:
            rows = await conn.fetch(sql, *params) if params else await conn.fetch(sql)
        return [_load(row["data"]) for row in rows]


def _load(raw: Any) -> dict:
    """asyncpg may return JSONB as str (default) — normalise to dict."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode()
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or {}


# ---------------------------------------------------------------------------
# Update operator application (in-Python; safer than trying to do complex
# JSONB path updates in SQL for our workload)
# ---------------------------------------------------------------------------
def _apply_update(doc: dict, update: dict) -> dict:
    doc = dict(doc)  # shallow copy
    if "$set" in update:
        for k, v in update["$set"].items():
            _set_path(doc, k, v)
    if "$push" in update:
        for k, v in update["$push"].items():
            arr = doc.setdefault(k, [])
            if not isinstance(arr, list):
                arr = []
            arr.append(v)
            doc[k] = arr
    # Support raw replacement (no ops) — Mongo semantics: replace whole doc.
    if not any(op in update for op in ("$set", "$push", "$unset", "$inc", "$addToSet")):
        return update
    if "$unset" in update:
        for k in update["$unset"]:
            _unset_path(doc, k)
    if "$inc" in update:
        for k, v in update["$inc"].items():
            doc[k] = (doc.get(k) or 0) + v
    return doc


def _set_path(doc: dict, dotted: str, value: Any) -> None:
    if "." not in dotted:
        doc[dotted] = value
        return
    parts = dotted.split(".")
    cur = doc
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _unset_path(doc: dict, dotted: str) -> None:
    if "." not in dotted:
        doc.pop(dotted, None)
        return
    parts = dotted.split(".")
    cur = doc
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return
        cur = cur[p]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


# ---------------------------------------------------------------------------
# Update result shims — Mongo call sites do `.modified_count`, `.upserted_id`.
# ---------------------------------------------------------------------------
class _UpdateResult:
    def __init__(self, matched: int, modified: int, upserted_id: Optional[str] = None):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id


class _InsertResult:
    def __init__(self, inserted_id: str):
        self.inserted_id = inserted_id


class _DeleteResult:
    def __init__(self, deleted: int):
        self.deleted_count = deleted


# ---------------------------------------------------------------------------
# Collection — mimics motor.AsyncIOMotorCollection for the subset we use.
# ---------------------------------------------------------------------------
class Collection:
    def __init__(self, name: str):
        self.name = name

    async def find_one(self, query: dict, projection: Optional[dict] = None) -> Optional[dict]:
        where, params = _query_to_sql(self.name, query)
        sql = f"SELECT data FROM docs WHERE {where} LIMIT 1"
        async with _pool_or_raise().acquire() as conn:
            row = await conn.fetchrow(sql, *params) if params else await conn.fetchrow(sql)
        return _load(row["data"]) if row else None

    def find(self, query: dict, projection: Optional[dict] = None) -> _Cursor:
        # Projection is intentionally ignored — we always return the whole doc.
        # Callers already tolerate extra fields; this keeps the adapter tiny.
        return _Cursor(self.name, query)

    async def count_documents(self, query: dict) -> int:
        where, params = _query_to_sql(self.name, query)
        sql = f"SELECT COUNT(*) AS n FROM docs WHERE {where}"
        async with _pool_or_raise().acquire() as conn:
            row = await conn.fetchrow(sql, *params) if params else await conn.fetchrow(sql)
        return int(row["n"]) if row else 0

    async def insert_one(self, doc: dict) -> _InsertResult:
        doc = dict(doc)
        doc.pop("_id", None)
        _id = _extract_id(self.name, doc)
        payload = json.dumps(doc, default=str)
        async with _pool_or_raise().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO docs (collection, id, data)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (collection, id) DO UPDATE
                    SET data = EXCLUDED.data, updated_at = NOW()
                """,
                self.name,
                _id,
                payload,
            )
        return _InsertResult(_id)

    async def update_one(
        self,
        query: dict,
        update: dict,
        upsert: bool = False,
    ) -> _UpdateResult:
        # Load the existing doc (if any), apply update ops in Python, write back.
        where, params = _query_to_sql(self.name, query)
        select_sql = f"SELECT id, data FROM docs WHERE {where} LIMIT 1"
        async with _pool_or_raise().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(select_sql, *params) if params else await conn.fetchrow(select_sql)
                if row:
                    doc = _load(row["data"])
                    new_doc = _apply_update(doc, update)
                    # Preserve the identity fields the query specified so we
                    # don't accidentally lose the primary key on $set-only ops.
                    for k, v in query.items():
                        if k == "_id" or k.startswith("$"):
                            continue
                        new_doc.setdefault(k, v)
                    _id = row["id"]
                    await conn.execute(
                        "UPDATE docs SET data = $3::jsonb, updated_at = NOW() "
                        "WHERE collection = $1 AND id = $2",
                        self.name,
                        _id,
                        json.dumps(new_doc, default=str),
                    )
                    modified = 0 if new_doc == doc else 1
                    return _UpdateResult(1, modified)

                if not upsert:
                    return _UpdateResult(0, 0)

                # Upsert path — synthesise the new doc from query + update ops.
                new_doc: dict = {}
                for k, v in query.items():
                    if k == "_id" or k.startswith("$"):
                        continue
                    new_doc[k] = v
                new_doc = _apply_update(new_doc, update)
                _id = _extract_id(self.name, new_doc)
                await conn.execute(
                    """
                    INSERT INTO docs (collection, id, data)
                    VALUES ($1, $2, $3::jsonb)
                    ON CONFLICT (collection, id) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = NOW()
                    """,
                    self.name,
                    _id,
                    json.dumps(new_doc, default=str),
                )
                return _UpdateResult(0, 0, upserted_id=_id)

    async def update_many(self, query: dict, update: dict) -> _UpdateResult:
        where, params = _query_to_sql(self.name, query)
        select_sql = f"SELECT id, data FROM docs WHERE {where}"
        modified = 0
        matched = 0
        async with _pool_or_raise().acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(select_sql, *params) if params else await conn.fetch(select_sql)
                for row in rows:
                    matched += 1
                    doc = _load(row["data"])
                    new_doc = _apply_update(doc, update)
                    if new_doc == doc:
                        continue
                    modified += 1
                    await conn.execute(
                        "UPDATE docs SET data = $3::jsonb, updated_at = NOW() "
                        "WHERE collection = $1 AND id = $2",
                        self.name,
                        row["id"],
                        json.dumps(new_doc, default=str),
                    )
        return _UpdateResult(matched, modified)

    async def delete_one(self, query: dict) -> _DeleteResult:
        where, params = _query_to_sql(self.name, query)
        sql = f"DELETE FROM docs WHERE {where} RETURNING id"
        async with _pool_or_raise().acquire() as conn:
            rows = await conn.fetch(sql, *params) if params else await conn.fetch(sql)
        return _DeleteResult(len(rows))

    async def delete_many(self, query: dict) -> _DeleteResult:
        return await self.delete_one(query)


# ---------------------------------------------------------------------------
# Database facade — `db.chains` / `db.settings` / etc.
# ---------------------------------------------------------------------------
class Database:
    def __getattr__(self, name: str) -> Collection:
        # Cache on first access so identity checks still work.
        c = Collection(name)
        setattr(self, name, c)
        return c

    def __getitem__(self, name: str) -> Collection:
        return getattr(self, name)


db = Database()
