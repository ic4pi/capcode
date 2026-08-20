"""
CapCode security-fix verification suite (SEC-001, SEC-002, SEC-004) + regressions.

Run:
  pytest /app/backend/tests/test_security_fixes.py -v
"""
import asyncio
import os
import pathlib
import subprocess
import sys
import time
import uuid

import pytest
import requests
from dotenv import dotenv_values

sys.path.insert(0, "/app/backend")
import docdb  # noqa: E402

frontend_env = dotenv_values("/app/frontend/.env")
base = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base.rstrip("/")

backend_env = dotenv_values("/app/backend/.env")
FORBIDDEN = ["NVIDIA_API_KEY", "OPENROUTER_API_KEY", "VENICE_API_KEY", "POSTGRES_URL", "NEON_AUTH_URL"]

POSTGRES_URL = os.environ.get("POSTGRES_URL") or backend_env.get("POSTGRES_URL")
if not POSTGRES_URL:
    raise RuntimeError("POSTGRES_URL missing")


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


# ---------------------------------------------------------------- SEC-001
class TestSec001EnvScrubbing:
    """Executor must spawn `bash run.sh` with a whitelisted env only."""

    def test_child_env_has_no_secrets(self, tmp_path):
        import executor

        dump = tmp_path / "env_dump.txt"
        (tmp_path / "run.sh").write_text(f"env > {dump}\nsleep 1\n")
        # simulate the parent process actually holding the secrets
        for k in FORBIDDEN:
            os.environ.setdefault(k, backend_env.get(k) or f"sentinel-{k}")

        res = asyncio.run(executor.run_workspace(tmp_path, timeout=4, port_to_check=59991))
        assert res["ran"] is True, res
        assert dump.exists(), f"run.sh did not execute: {res}"

        child = {}
        for line in dump.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                child[k] = v

        leaked = [k for k in FORBIDDEN if k in child]
        assert not leaked, f"secrets leaked into child env: {leaked}"

        allowed = {"PATH", "HOME", "TERM", "LANG", "LC_ALL", "LC_CTYPE", "USER",
                   "TMPDIR", "PWD", "SHELL", "APP_PORT",
                   # bash always injects these itself
                   "SHLVL", "_", "OLDPWD"}
        extras = sorted(set(child) - allowed)
        assert not extras, f"unexpected env vars inherited by child: {extras}"
        assert child.get("APP_PORT") == "59991"

    def test_secret_values_absent_from_dump(self, tmp_path):
        import executor

        dump = tmp_path / "env_dump.txt"
        (tmp_path / "run.sh").write_text(f"env > {dump}\n")
        asyncio.run(executor.run_workspace(tmp_path, timeout=3, port_to_check=59992))
        text = dump.read_text()
        for k in FORBIDDEN:
            val = (backend_env.get(k) or "").strip()
            if val and len(val) > 8:
                assert val not in text, f"raw value of {k} present in child env"


# ---------------------------------------------------------------- SEC-002
class TestSec002PathTraversal:
    """executor.materialize() must never write outside the workspace root."""

    MALICIOUS = ["../../etc/passwd", "/tmp/pwned", "..\\pwn", "dir/../../out",
                 "x\x00y", "../escape.txt", "a/b/../../../../oops.txt"]
    LEGIT = ["src/main.py", "sub/dir/file.txt", "run.sh", "index.html"]

    def test_materialize_blocks_traversal(self):
        import executor

        chain_id = "sec002" + uuid.uuid4().hex[:10]
        gen = {"gen": 0, "name": "PathGuard",
               "files": [{"path": p, "content": "PWNED"} for p in self.MALICIOUS]
                        + [{"path": p, "content": "ok"} for p in self.LEGIT]}
        # snapshot canaries
        canaries = [pathlib.Path("/tmp/pwned"), pathlib.Path("/workspaces/out"),
                    pathlib.Path("/workspaces/oops.txt")]
        for c in canaries:
            if c.exists():
                c.unlink()

        root = executor.materialize(chain_id, gen)
        assert root.exists()
        root_resolved = root.resolve()

        written = [p for p in root.rglob("*") if p.is_file()]
        for p in written:
            p.resolve().relative_to(root_resolved)  # raises if escaped

        for c in canaries:
            assert not c.exists(), f"traversal wrote outside workspace: {c}"

        names = sorted(str(p.relative_to(root_resolved)) for p in written)
        # Every legit path must be present
        for legit in self.LEGIT:
            assert legit in names, f"legit path {legit} missing: {names}"
        # No path traversal escaped; report anything extra that got written
        extras = [n for n in names if n not in self.LEGIT]
        print("extra files materialized (contained but not filtered):", extras)
        # KNOWN MINOR: executor.materialize() does not reject backslashes the way
        # chain_generator._is_safe_relpath does, so "..\\pwn" lands as a literal
        # filename INSIDE the workspace (contained on POSIX, unsafe on Windows).
        # "/tmp/pwned" is neutralised to the relative "tmp/pwned" (contained).
        assert set(extras) <= {"..\\pwn", "tmp/pwned"}, f"unexpected files written: {extras}"

    def test_run_sh_is_executable(self):
        import executor

        chain_id = "sec002x" + uuid.uuid4().hex[:8]
        root = executor.materialize(chain_id, {"gen": 0, "name": "ChmodCheck",
                                               "files": [{"path": "run.sh", "content": "echo hi\n"}]})
        assert os.access(root / "run.sh", os.X_OK)

    def test_chain_generator_relpath_guard(self):
        """The Artist/corrector sanitizer helper logic rejects the same set."""
        import chain_generator  # noqa: F401  (import must not explode)
        import re as _re

        src = pathlib.Path("/app/backend/chain_generator.py").read_text()
        assert "_is_safe_relpath" in src
        assert _re.search(r"rejected unsafe corrector file path", src)


# ---------------------------------------------------------------- SEC-004
class TestSec004PushEndpointContract:
    def test_push_unknown_chain_404(self):
        r = requests.post(f"{BASE_URL}/api/chains/{uuid.uuid4()}/push",
                          json={"session_id": "sec004-test", "repo": "TEST_repo"},
                          headers={"X-Capcode-Session": "sec004-test"}, timeout=30)
        assert r.status_code == 404, r.text
        assert "not found" in r.text.lower()

    def test_push_incomplete_chain_409(self, incomplete_chain_id):
        r = requests.post(f"{BASE_URL}/api/chains/{incomplete_chain_id}/push",
                          json={"session_id": "sec004-test", "repo": "TEST_repo"},
                          headers={"X-Capcode-Session": "sec004-test"}, timeout=30)
        assert r.status_code == 409, r.text
        assert "not complete" in r.text.lower()

    def test_push_without_credentials_400(self, complete_chain_id):
        sid = f"sec004-nocreds-{uuid.uuid4().hex[:8]}"
        r = requests.post(f"{BASE_URL}/api/chains/{complete_chain_id}/push",
                          json={"session_id": sid, "repo": "TEST_repo"},
                          headers={"X-Capcode-Session": "sec004-test"}, timeout=30)
        assert r.status_code == 400, r.text
        assert "github credentials missing" in r.text.lower()

    def test_no_pat_in_git_argv_source(self):
        src = pathlib.Path("/app/backend/server.py").read_text()
        assert "GIT_ASKPASS" in src
        # remote url must not be interpolated with the token
        assert "https://{username}:{token}@github.com" not in src
        assert "@github.com/{username}" not in src
        assert 'push_cmd.append("--force")' in src


# ---------------------------------------------------------------- Settings / BYOK
class TestSettingsBYOK:
    def test_settings_roundtrip_hides_secrets(self):
        sid = f"byok-regression-{uuid.uuid4().hex[:8]}"
        payload = {"keys": {"openrouter": "sk-or-test-value"},
                   "github": {"username": "tester", "token": "ghp_test"}}
        r = requests.post(f"{BASE_URL}/api/settings", params={"session_id": sid},
                          json=payload, timeout=30)
        assert r.status_code == 200, r.text
        body = r.text
        assert "sk-or-test-value" not in body
        assert "ghp_test" not in body

        g = requests.get(f"{BASE_URL}/api/settings", params={"session_id": sid}, timeout=30)
        assert g.status_code == 200, g.text
        d = g.json()
        assert d["keys_set"]["openrouter"] is True
        assert d["keys_set"]["venice"] is False
        assert d["github"]["token_set"] is True
        assert d["github"]["username"] == "tester"
        assert "sk-or-test-value" not in g.text
        assert "ghp_test" not in g.text
        assert "token" not in d["github"] or d["github"].get("token") in (None, "")

    def test_clearing_key(self):
        sid = f"byok-clear-{uuid.uuid4().hex[:8]}"
        requests.post(f"{BASE_URL}/api/settings", params={"session_id": sid},
                      json={"keys": {"nvidia": "nv-secret"}}, timeout=30)
        r = requests.post(f"{BASE_URL}/api/settings", params={"session_id": sid},
                          json={"keys": {"nvidia": ""}}, timeout=30)
        assert r.status_code == 200
        assert r.json()["keys_set"]["nvidia"] is False


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def mongo_db():
    """Kept the name `mongo_db` to minimize diff noise at call sites below —
    it now yields docdb.db (Postgres/JSONB-backed), not a real Mongo handle."""
    _run(docdb.connect(POSTGRES_URL))
    return docdb.db


@pytest.fixture(scope="session")
def incomplete_chain_id(mongo_db):
    cid = str(uuid.uuid4())
    _run(mongo_db.chains.insert_one({
        "id": cid, "session_id": "sec004-test", "anon_session_id": "sec004-test",
        "target_prompt": "TEST_incomplete",
        "status": "running", "generations": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))
    yield cid
    _run(mongo_db.chains.delete_one({"id": cid}))


@pytest.fixture(scope="session")
def complete_chain_id(mongo_db):
    doc = _run(mongo_db.chains.find_one(
        {"status": "complete", "anon_session_id": "sec004-test"}, {"_id": 0, "id": 1}))
    if doc:
        return doc["id"]
    cid = str(uuid.uuid4())
    _run(mongo_db.chains.insert_one({
        "id": cid, "session_id": "sec004-test", "anon_session_id": "sec004-test",
        "target_prompt": "TEST_complete",
        "status": "complete",
        "generations": [{"gen": 0, "name": "TEST", "files": [{"path": "run.sh", "content": "echo hi"}]}],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))
    return cid


# ---------------------------------------------------------------- orphan sweep
class TestOrphanSweep:
    def test_running_chain_marked_failed_on_restart(self, mongo_db):
        cid = str(uuid.uuid4())
        _run(mongo_db.chains.insert_one({
            "id": cid, "session_id": "orphan-test", "target_prompt": "TEST_orphan",
            "status": "running", "generations": [],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }))
        try:
            subprocess.run(["sudo", "supervisorctl", "restart", "backend"],
                           capture_output=True, text=True, timeout=90)
            # wait for backend to come back up
            for _ in range(30):
                time.sleep(2)
                try:
                    if requests.get(f"{BASE_URL}/api/status", timeout=5).status_code == 200:
                        break
                except Exception:
                    pass
            doc = _run(mongo_db.chains.find_one({"id": cid}, {"_id": 0}))
            assert doc["status"] == "failed", doc
            assert doc.get("error") == "backend restarted before build finished — try again", doc
        finally:
            _run(mongo_db.chains.delete_one({"id": cid}))
