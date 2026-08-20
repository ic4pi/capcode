"""Iteration 9 — billing.get_cycle() lazy 30-day renewal (annual plan support).

Unit-level against the real Postgres-backed docdb adapter, with a TEST_ user id
so no seeded/production data is touched.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")
import billing  # noqa: E402
import docdb  # noqa: E402

TEST_USER = "TEST_it9_billing_user"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(scope="module")
def db():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(docdb.connect(os.environ["POSTGRES_URL"]))
    yield docdb.db
    loop.run_until_complete(docdb.db.billing_cycles.delete_many({"user_id": TEST_USER}))


# --- get_cycle contract ---
def test_start_cycle_then_get_cycle_clean_doc(db):
    started = _run(billing.start_cycle(db, TEST_USER))
    assert started["status"] == "active"
    cycle = _run(billing.get_cycle(db, TEST_USER))
    assert cycle is not None
    assert "_id" not in cycle
    assert cycle["api_budget_credits"] == billing.API_BUDGET_CREDITS
    assert cycle["recursion_fund_credits"] == billing.RECURSION_FUND_CREDITS


# --- lazy 30-day renewal (annual subscribers get monthly refills) ---
def test_get_cycle_lazily_renews_after_30_days(db):
    _run(billing.start_cycle(db, TEST_USER))
    past_end = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _run(db.billing_cycles.update_one(
        {"user_id": TEST_USER},
        {"$set": {"cycle_end": past_end, "api_budget_credits": 0, "recursion_fund_credits": 0}},
    ))
    renewed = _run(billing.get_cycle(db, TEST_USER))
    assert renewed["api_budget_credits"] == billing.API_BUDGET_CREDITS, "credits not refilled"
    assert renewed["recursion_fund_credits"] == billing.RECURSION_FUND_CREDITS
    assert datetime.fromisoformat(renewed["cycle_end"]) > datetime.now(timezone.utc)


# --- cancellation ---
def test_cancel_cycle_makes_get_cycle_none(db):
    _run(billing.start_cycle(db, TEST_USER))
    _run(billing.cancel_cycle(db, TEST_USER))
    assert _run(billing.get_cycle(db, TEST_USER)) is None
