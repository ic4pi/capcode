"""Emergent-managed Google Auth tests (optional identity layer).

Covers: POST /api/auth/session validation contract, GET /api/auth/me
(cookie + bearer), POST /api/auth/logout, expired sessions, CORS credentials.
The real Emergent exchange is not reachable from the test env, so sessions are
seeded directly through docdb (per /app/auth_testing.md).
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import dotenv_values

sys.path.insert(0, "/app/backend")
import docdb  # noqa: E402

frontend_env = dotenv_values("/app/frontend/.env")
backend_env = dotenv_values("/app/backend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
API = f"{BASE_URL}/api"

POSTGRES_URL = os.environ.get("POSTGRES_URL") or backend_env.get("POSTGRES_URL")
if not POSTGRES_URL:
    raise RuntimeError("POSTGRES_URL missing")

VALID_TOKEN = "MY_TEST_TOKEN"
EXPIRED_TOKEN = "MY_TEST_TOKEN_EXPIRED"
LOGOUT_TOKEN = "MY_TEST_TOKEN_LOGOUT"
TEST_USER_ID = "user_TESTAUTH"
TEST_EMAIL = "test@capcode.dev"


def _run(coro):
    """Run a docdb coroutine from a sync pytest test/fixture. Resilient to a
    closed/missing event loop (e.g. after another test's `asyncio.run()`)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed loop")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# --- fixtures -------------------------------------------------------------
@pytest.fixture(scope="module")
def mongo_db():
    """Kept the name `mongo_db` to minimize diff noise at call sites below —
    it now yields docdb.db (Postgres/JSONB-backed), not a real Mongo handle."""
    _run(docdb.connect(POSTGRES_URL))
    yield docdb.db
    _run(docdb.close())


def _seed_session(db, token, days=7):
    now = datetime.now(timezone.utc)
    _run(db.users.update_one(
        {"user_id": TEST_USER_ID},
        {"$set": {
            "user_id": TEST_USER_ID,
            "email": TEST_EMAIL,
            "name": "Test User",
            "picture": "https://placehold.co/64",
            "created_at": now.isoformat(),
        }},
        upsert=True,
    ))
    _run(db.user_sessions.update_one(
        {"session_token": token},
        {"$set": {
            "session_token": token,
            "user_id": TEST_USER_ID,
            "expires_at": (now + timedelta(days=days)).isoformat(),
            "created_at": now.isoformat(),
        }},
        upsert=True,
    ))


@pytest.fixture(scope="module", autouse=True)
def seeded(mongo_db):
    _seed_session(mongo_db, VALID_TOKEN, days=7)
    _seed_session(mongo_db, LOGOUT_TOKEN, days=7)
    _seed_session(mongo_db, EXPIRED_TOKEN, days=-1)
    yield
    # docdb has no `$in` support, so delete each token individually — same
    # net effect as the original `delete_many({"$in": [...]})`.
    for tok in (VALID_TOKEN, EXPIRED_TOKEN, LOGOUT_TOKEN):
        _run(mongo_db.user_sessions.delete_one({"session_token": tok}))
    _run(mongo_db.users.delete_one({"email": TEST_EMAIL}))


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    return s


# --- POST /api/auth/session validation contract ---------------------------
class TestAuthSession:
    def test_missing_session_id_returns_400(self, client):
        r = client.post(f"{API}/auth/session", json={})
        assert r.status_code == 400, r.text
        assert r.json().get("detail") == "session_id required"

    def test_missing_session_id_no_body_returns_400(self, client):
        r = client.post(f"{API}/auth/session")
        assert r.status_code == 400, r.text
        assert r.json().get("detail") == "session_id required"

    def test_bogus_session_id_header_form_returns_401(self, client):
        r = client.post(f"{API}/auth/session", headers={"X-Session-ID": "bogus-abc-123"})
        assert r.status_code == 401, r.text
        assert str(r.json().get("detail", "")).startswith("emergent auth exchange failed")

    def test_bogus_session_id_body_form_returns_401(self, client):
        r = client.post(f"{API}/auth/session", json={"session_id": "bogus-abc-123"})
        assert r.status_code == 401, r.text
        assert str(r.json().get("detail", "")).startswith("emergent auth exchange failed")


# --- GET /api/auth/me -----------------------------------------------------
class TestAuthMe:
    def test_me_without_credentials_returns_401(self, client):
        r = requests.get(f"{API}/auth/me")
        assert r.status_code == 401, r.text
        assert r.json().get("detail") == "not authenticated"

    def test_me_with_bearer_returns_authuser(self):
        r = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["user_id"] == TEST_USER_ID
        assert data["email"] == TEST_EMAIL
        assert data["name"] == "Test User"
        assert data["picture"] == "https://placehold.co/64"
        assert "_id" not in data
        assert set(data.keys()) == {"user_id", "email", "name", "picture"}

    def test_me_with_cookie_returns_authuser(self):
        r = requests.get(f"{API}/auth/me", cookies={"session_token": VALID_TOKEN})
        assert r.status_code == 200, r.text
        assert r.json()["email"] == TEST_EMAIL

    def test_me_with_expired_session_returns_401(self):
        r = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {EXPIRED_TOKEN}"})
        assert r.status_code == 401, r.text

    def test_me_with_unknown_token_returns_401(self):
        r = requests.get(f"{API}/auth/me", headers={"Authorization": "Bearer no-such-token"})
        assert r.status_code == 401, r.text


# --- POST /api/auth/logout ------------------------------------------------
class TestAuthLogout:
    def test_logout_deletes_session_and_me_401(self, mongo_db):
        # Pre-condition: token works
        assert requests.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {LOGOUT_TOKEN}"}
        ).status_code == 200

        r = requests.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {LOGOUT_TOKEN}"})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        # Cookie cleared
        assert "session_token=" in (r.headers.get("set-cookie") or "")

        # Session row gone
        assert _run(mongo_db.user_sessions.find_one({"session_token": LOGOUT_TOKEN})) is None

        after = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {LOGOUT_TOKEN}"})
        assert after.status_code == 401, after.text

    def test_logout_without_token_is_noop_ok(self):
        r = requests.post(f"{API}/auth/logout")
        assert r.status_code == 200
        assert r.json() == {"ok": True}


# --- CORS credentials -----------------------------------------------------
class TestCors:
    ORIGIN = "https://self-learning-maker.preview.emergentagent.com"

    APP_URL = "http://localhost:8001"  # bypasses the CDN/ingress edge

    def test_app_echoes_origin_with_credentials(self):
        """App-level (uvicorn) CORS: origin must be echoed, never '*'."""
        r = requests.get(
            f"{self.APP_URL}/api/auth/me",
            headers={"Origin": self.ORIGIN, "Authorization": f"Bearer {VALID_TOKEN}"},
        )
        assert r.status_code == 200, r.text
        assert r.headers.get("access-control-allow-credentials") == "true"
        assert r.headers.get("access-control-allow-origin") == self.ORIGIN

    def test_public_edge_cors_credentials(self):
        """Through the public URL the CDN/ingress rewrites ACAO to '*' (infra,
        not app code). Only allow-credentials is asserted here; frontend calls
        are same-origin so this does not break cookie auth."""
        r = requests.get(
            f"{API}/auth/me",
            headers={"Origin": self.ORIGIN, "Authorization": f"Bearer {VALID_TOKEN}"},
        )
        assert r.status_code == 200, r.text
        assert r.headers.get("access-control-allow-credentials") == "true"
        if r.headers.get("access-control-allow-origin") == "*":
            print("WARNING: ingress edge returns ACAO '*' with credentials (infra-level)")

    def test_cors_preflight_app_level(self):
        r = requests.options(
            f"{self.APP_URL}/api/auth/me",
            headers={
                "Origin": self.ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert r.status_code in (200, 204), r.text
        assert r.headers.get("access-control-allow-origin") == self.ORIGIN
        assert r.headers.get("access-control-allow-credentials") == "true"


# --- Regression: anonymous scheme untouched by auth -----------------------
class TestAnonymousRegression:
    def test_settings_works_without_auth(self):
        sid = "TEST_anon_regression_sid"
        r = requests.get(f"{API}/settings", params={"session_id": sid})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["session_id"] == sid
        assert "keys_set" in data
        assert "_id" not in data

    def test_root_and_status_public(self):
        for path in ("/", "/status"):
            r = requests.get(f"{API}{path}")
            assert r.status_code == 200, f"{path} -> {r.status_code}"
