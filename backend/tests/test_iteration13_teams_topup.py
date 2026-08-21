"""Iteration 13 — Default LLM teams, 6-role settings, credit top-ups, profile auth."""
import os
import uuid

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

ROLES = ("teacher", "architect", "artist", "reviewer", "rater", "corrector")


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def session_id():
    return f"TEST-{uuid.uuid4()}"


# --- module: server.py /api/teams/defaults ---
class TestTeamDefaults:
    def test_defaults_public(self, client):
        r = client.get(f"{BASE_URL}/api/teams/defaults", timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert set(data) == {"venice", "openrouter", "nvidia"}
        assert data["venice"]["uncensored"] is True
        assert data["openrouter"]["uncensored"] is False
        assert data["nvidia"]["uncensored"] is False
        for name, team in data.items():
            assert set(team["assignments"]) == set(ROLES), name
            assert team.get("label") and team.get("description"), name
            for role, a in team["assignments"].items():
                assert a.get("provider") and a.get("model"), (name, role)
        assert all(a["provider"] == "venice" for a in data["venice"]["assignments"].values())
        assert all(a["provider"] == "nvidia" for a in data["nvidia"]["assignments"].values())
        assert all(a["provider"] == "openrouter" for a in data["openrouter"]["assignments"].values())


# --- module: server.py /api/settings (6 roles) + /api/teams/reset ---
class TestSettingsSixRoles:
    def test_settings_returns_six_roles(self, client, session_id):
        r = client.get(f"{BASE_URL}/api/settings", params={"session_id": session_id}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        for role in ROLES:
            assert role in data and data[role].get("provider"), role
        assert "_id" not in data
        assert data["session_id"] == session_id
        assert set(data["keys_set"]) == {"openrouter", "venice", "nvidia"}

    def test_reset_nvidia_and_persist(self, client, session_id):
        defaults = client.get(f"{BASE_URL}/api/teams/defaults", timeout=30).json()
        r = client.post(f"{BASE_URL}/api/teams/reset",
                        json={"team": "nvidia", "session_id": session_id}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        for role in ROLES:
            assert data[role] == defaults["nvidia"]["assignments"][role], role
        # GET to verify persistence
        g = client.get(f"{BASE_URL}/api/settings", params={"session_id": session_id}, timeout=30).json()
        for role in ROLES:
            assert g[role] == defaults["nvidia"]["assignments"][role], role

    def test_reset_venice_overwrites(self, client, session_id):
        defaults = client.get(f"{BASE_URL}/api/teams/defaults", timeout=30).json()
        r = client.post(f"{BASE_URL}/api/teams/reset",
                        json={"team": "venice", "session_id": session_id}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        g = client.get(f"{BASE_URL}/api/settings", params={"session_id": session_id}, timeout=30).json()
        for role in ROLES:
            assert g[role] == defaults["venice"]["assignments"][role], role

    def test_reset_openrouter(self, client, session_id):
        defaults = client.get(f"{BASE_URL}/api/teams/defaults", timeout=30).json()
        r = client.post(f"{BASE_URL}/api/teams/reset",
                        json={"team": "openrouter", "session_id": session_id}, timeout=30)
        assert r.status_code == 200
        for role in ROLES:
            assert r.json()[role] == defaults["openrouter"]["assignments"][role], role

    def test_reset_unknown_team_400(self, client, session_id):
        r = client.post(f"{BASE_URL}/api/teams/reset",
                        json={"team": "bogus", "session_id": session_id}, timeout=30)
        assert r.status_code == 400, r.text[:300]
        assert "bogus" in r.text or "must be one of" in r.text

    def test_reset_missing_fields_422(self, client):
        r = client.post(f"{BASE_URL}/api/teams/reset", json={"team": "nvidia"}, timeout=30)
        assert r.status_code == 422, r.text[:300]

    def test_reset_does_not_clobber_keys(self, client):
        sid = f"TEST-{uuid.uuid4()}"
        client.post(f"{BASE_URL}/api/settings", params={"session_id": sid},
                    json={"keys": {"nvidia": "TEST_KEY_123"}, "github": {"username": "TEST_user"}}, timeout=30)
        client.post(f"{BASE_URL}/api/teams/reset", json={"team": "venice", "session_id": sid}, timeout=30)
        g = client.get(f"{BASE_URL}/api/settings", params={"session_id": sid}, timeout=30).json()
        assert g["keys_set"]["nvidia"] is True, "team reset wiped BYOK keys"
        assert g["github"]["username"] == "TEST_user"

    def test_save_all_six_roles(self, client):
        sid = f"TEST-{uuid.uuid4()}"
        payload = {r: {"provider": "nvidia", "model": f"m-{r}"} for r in ROLES}
        r = client.post(f"{BASE_URL}/api/settings", params={"session_id": sid}, json=payload, timeout=30)
        assert r.status_code == 200, r.text[:300]
        g = client.get(f"{BASE_URL}/api/settings", params={"session_id": sid}, timeout=30).json()
        for role in ROLES:
            assert g[role]["model"] == f"m-{role}", role


# --- module: tiers.py top-up endpoints ---
class TestTopups:
    def test_packages_public(self, client):
        r = client.get(f"{BASE_URL}/api/tier/topup/packages", timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert set(data) == {"8", "16", "32"}
        assert data["8"]["usd"] == 8 and data["8"]["credits"] == 500
        assert data["16"]["usd"] == 16 and data["16"]["credits"] == 1000
        assert data["32"]["usd"] == 32 and data["32"]["credits"] == 2000

    @pytest.mark.parametrize("pkg", ["8", "16", "32"])
    def test_checkout_requires_signin(self, client, pkg):
        r = client.post(f"{BASE_URL}/api/tier/topup/checkout", json={"package": pkg}, timeout=30)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}: {r.text[:300]}"
        detail = r.json().get("detail", "")
        assert isinstance(detail, str) and detail.strip(), "empty error message"

    def test_checkout_bad_package_not_500(self, client):
        r = client.post(f"{BASE_URL}/api/tier/topup/checkout", json={"package": "999"}, timeout=30)
        assert r.status_code in (400, 401, 403), r.text[:300]

    def test_checkout_missing_body(self, client):
        r = client.post(f"{BASE_URL}/api/tier/topup/checkout", json={}, timeout=30)
        assert r.status_code in (400, 401, 403, 422), r.text[:300]


# --- module: server.py /api/profile auth gate ---
class TestProfileAuth:
    def test_profile_unauthenticated_401(self, client):
        r = client.get(f"{BASE_URL}/api/profile", timeout=30)
        assert r.status_code == 401, r.text[:300]
        assert "sign in" in r.text.lower()

    def test_profile_with_anon_session_still_401(self, client):
        r = client.get(f"{BASE_URL}/api/profile",
                       headers={"X-Capcode-Session": str(uuid.uuid4())}, timeout=30)
        assert r.status_code == 401, r.text[:300]

    def test_profile_bogus_bearer_401(self, client):
        r = client.get(f"{BASE_URL}/api/profile",
                       headers={"Authorization": "Bearer not.a.jwt"}, timeout=30)
        assert r.status_code == 401, r.text[:300]


# --- smoke: app still boots after dead-code cleanup ---
class TestBootSmoke:
    def test_status(self, client):
        r = client.get(f"{BASE_URL}/api/status", timeout=30)
        assert r.status_code == 200 and "providers" in r.json()

    def test_tier_plans(self, client):
        r = client.get(f"{BASE_URL}/api/tier/plans", timeout=30)
        assert r.status_code in (200, 404)

    def test_frontend_serves(self, client):
        r = client.get(BASE_URL, timeout=30)
        assert r.status_code == 200 and "<div id=\"root\"" in r.text
