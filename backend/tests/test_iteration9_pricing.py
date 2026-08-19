"""Iteration 9 — tier pricing endpoints (/api/tier/prices, /checkout, /start-trial).

Anonymous-only coverage: signed-in checkout completion needs a real Neon Auth
JWT (no backdoor), so we only assert the auth gate + public price contract.
"""
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

SESSION_ID = "TEST_it9_session"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "X-Capcode-Session": SESSION_ID})
    return s


# --- GET /api/tier/prices (public) ---
class TestPrices:
    def test_prices_shape_and_values(self, client):
        r = client.get(f"{BASE_URL}/api/tier/prices", timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        for plan in ("trial", "paid", "paid_annual"):
            assert plan in d, f"missing plan {plan}: {d}"
            assert isinstance(d[plan]["amount"], (int, float))
            assert d[plan]["currency"] == "usd"
        assert d["trial"]["amount"] == 2.99
        assert d["trial"]["interval"] == "one_time"
        assert d["paid"]["amount"] == 16.0
        assert d["paid"]["interval"] == "month"
        assert d["paid_annual"]["amount"] == 192.0
        assert d["paid_annual"]["interval"] == "year"

    def test_prices_no_auth_required_and_cached_consistent(self):
        r1 = requests.get(f"{BASE_URL}/api/tier/prices", timeout=30)
        r2 = requests.get(f"{BASE_URL}/api/tier/prices", timeout=30)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == r2.json()

    def test_prices_leaks_no_mongo_id(self, client):
        assert "_id" not in client.get(f"{BASE_URL}/api/tier/prices", timeout=30).text


# --- POST /api/tier/checkout ---
class TestCheckoutAuthGate:
    @pytest.mark.parametrize("plan", ["paid", "paid_annual", "trial", "bogus", None])
    def test_requires_sign_in(self, client, plan):
        body = {} if plan is None else {"plan": plan}
        r = client.post(f"{BASE_URL}/api/tier/checkout", json=body, timeout=30)
        assert r.status_code == 401, f"plan={plan} -> {r.status_code} {r.text[:200]}"
        assert "sign in" in r.json().get("detail", "").lower()

    def test_checkout_no_body_still_401_not_500(self, client):
        r = requests.post(f"{BASE_URL}/api/tier/checkout",
                          headers={"X-Capcode-Session": SESSION_ID}, timeout=30)
        assert r.status_code == 401, r.text[:200]


# --- POST /api/tier/start-trial (deprecated behind ALLOW_MOCK_BILLING) ---
class TestStartTrialGating:
    def test_start_trial_gate(self, client):
        r = client.post(f"{BASE_URL}/api/tier/start-trial", json={}, timeout=30)
        # ALLOW_MOCK_BILLING=true in this env -> gate passes, auth gate returns 401.
        # With the flag off it must be 410.
        assert r.status_code in (401, 410), r.text[:200]
        if r.status_code == 410:
            assert "checkout" in r.json()["detail"]


# --- GET /api/tier/status (anonymous) ---
class TestTierStatus:
    def test_anonymous_is_free(self, client):
        r = client.get(f"{BASE_URL}/api/tier/status", timeout=30)
        assert r.status_code == 200, r.text[:200]
        d = r.json()
        assert d["tier"] == "free"
        assert d["trial_days_remaining"] is None
