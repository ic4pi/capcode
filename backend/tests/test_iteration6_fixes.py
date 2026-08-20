"""Iteration 6 retest — (1) workspace endpoint ownership, (2) rater non-degenerate scores.

Runs 3 real builds concurrently (slow, ~4 min) then asserts on rewards +
workspace ownership using one of the completed chains.
"""
import os
import pathlib
import re
import subprocess
import threading
import time
import uuid

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or frontend_env["REACT_APP_BACKEND_URL"]).rstrip("/")

OWNER_A = f"TEST_it6_ownerA_{uuid.uuid4()}"
OWNER_B = f"TEST_it6_ownerB_{uuid.uuid4()}"

TARGETS = [
    "a simple webgl page that draws a black background with a red rotating cube",
    "a single page html digital clock with a neon green readout",
    "a tiny html page with a button that shows a random dice roll",
]

_results: dict = {}
_ws: dict = {}


def mongo(js):
    r = subprocess.run(["mongosh", "--quiet", "--eval", f"use('test_database');{js}"],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def _build(target, idx):
    try:
        r = requests.post(f"{BASE_URL}/api/evolve",
                          headers={"X-Capcode-Session": OWNER_A},
                          json={"target_prompt": target}, timeout=60)
        if r.status_code != 200:
            _results[idx] = {"error": f"evolve {r.status_code} {r.text[:200]}"}
            return
        cid = r.json()["id"]
        deadline = time.time() + 300
        doc = None
        while time.time() < deadline:
            g = requests.get(f"{BASE_URL}/api/chains/{cid}",
                             headers={"X-Capcode-Session": OWNER_A}, timeout=30)
            if g.status_code != 200:
                _results[idx] = {"error": f"get {g.status_code}"}
                return
            doc = g.json()
            if doc.get("status") in ("complete", "failed"):
                break
            time.sleep(5)
        _results[idx] = {"chain": doc, "id": cid}
    except Exception as exc:  # noqa: BLE001
        _results[idx] = {"error": str(exc)}


@pytest.fixture(scope="module", autouse=True)
def builds():
    threads = [threading.Thread(target=_build, args=(t, i)) for i, t in enumerate(TARGETS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=360)
    yield _results
    mongo("db.chains.deleteMany({anon_session_id: /TEST_it6_/});")


def _completed():
    for idx, res in sorted(_results.items()):
        doc = (res or {}).get("chain") or {}
        if doc.get("status") == "complete" and doc.get("generations"):
            return doc
    return None


# --- Fix #2: rater returns real, differentiated, non-zero scores -------------
KEYS = ("helpfulness", "correctness", "coherence", "complexity", "verbosity")


@pytest.mark.parametrize("idx", [0, 1, 2])
def test_build_reward_not_all_zero(idx):
    res = _results.get(idx) or {}
    assert "error" not in res, res.get("error")
    doc = res.get("chain")
    assert doc, "no chain doc"
    print(f"[build {idx}] status={doc.get('status')} error={doc.get('error')}")
    assert doc.get("status") == "complete", f"status={doc.get('status')} err={doc.get('error')}"
    gen = doc["generations"][0]
    reward = gen.get("reward") or {}
    comp = gen.get("composite_score")
    print(f"[build {idx}] reward={reward} composite={comp} started={(gen.get('exec') or {}).get('started')}")
    assert set(KEYS).issubset(reward.keys()), reward
    vals = [float(reward[k]) for k in KEYS]
    assert not all(v == 0.0 for v in vals), f"all-zero reward: {reward}"
    for v in vals:
        assert 0.0 <= v <= 4.0, f"score out of 0-4 range: {reward}"
    assert comp is not None and comp > 0.5, f"composite too low: {comp} reward={reward}"


def test_no_completed_chain_has_negative_or_zero_composite():
    for idx, res in sorted(_results.items()):
        doc = (res or {}).get("chain") or {}
        if doc.get("status") != "complete":
            continue
        for g in doc.get("generations", []):
            comp = g.get("composite_score")
            assert comp is not None and comp > 0.5, f"chain {doc.get('id')} composite={comp}"


def test_exec_started_and_real_files():
    doc = _completed()
    assert doc, "no completed chain to inspect"
    gen = doc["generations"][0]
    files = {f["path"]: f["content"] for f in gen.get("files", [])}
    print("files:", list(files))
    assert files, "no files"
    assert any(len(c.strip()) > 100 for c in files.values()), "all files look like stubs"
    print("exec:", {k: v for k, v in (gen.get("exec") or {}).items() if k != "stdout"})


# --- Fix #1: workspace endpoint ownership -----------------------------------
class TestWorkspaceOwnership:
    def test_owner_can_materialize(self):
        doc = _completed()
        assert doc, "no completed chain"
        cid = doc["id"]
        r = requests.post(f"{BASE_URL}/api/chains/{cid}/workspace/1",
                          headers={"X-Capcode-Session": OWNER_A}, timeout=60)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        d = r.json()
        assert d.get("workspace_path"), d
        assert isinstance(d.get("files"), list) and d["files"], d
        _ws["path"] = d["workspace_path"]
        print("workspace:", d["workspace_path"], d["files"])

    def test_non_owner_gets_404_and_writes_nothing(self):
        doc = _completed()
        assert doc, "no completed chain"
        cid = doc["id"]
        # snapshot the victim's workspace dir mtimes
        base = pathlib.Path(f"/workspaces/{cid[:8]}")
        before = {p: p.stat().st_mtime_ns for p in base.rglob("*")} if base.exists() else {}
        time.sleep(1.1)
        r = requests.post(f"{BASE_URL}/api/chains/{cid}/workspace/1",
                          headers={"X-Capcode-Session": OWNER_B}, timeout=30)
        assert r.status_code == 404, f"workspace leaked: {r.status_code} {r.text[:300]}"
        assert r.json().get("detail") == "chain not found", r.json()
        after = {p: p.stat().st_mtime_ns for p in base.rglob("*")} if base.exists() else {}
        assert set(after) == set(before), f"intruder created files: {set(after) - set(before)}"
        changed = [str(p) for p in after if before.get(p) != after[p]]
        assert not changed, f"intruder rewrote files: {changed}"

    def test_no_identity_gets_404(self):
        doc = _completed()
        assert doc, "no completed chain"
        cid = doc["id"]
        base = pathlib.Path(f"/workspaces/{cid[:8]}")
        before = {p: p.stat().st_mtime_ns for p in base.rglob("*")} if base.exists() else {}
        time.sleep(1.1)
        r = requests.post(f"{BASE_URL}/api/chains/{cid}/workspace/1", timeout=30)
        assert r.status_code == 404, f"{r.status_code} {r.text[:200]}"
        after = {p: p.stat().st_mtime_ns for p in base.rglob("*")} if base.exists() else {}
        changed = [str(p) for p in after if before.get(p) != after[p]]
        assert set(after) == set(before) and not changed, f"anon write: {changed}"

    def test_unknown_chain_404(self):
        r = requests.post(f"{BASE_URL}/api/chains/{uuid.uuid4()}/workspace/1",
                          headers={"X-Capcode-Session": OWNER_A}, timeout=30)
        assert r.status_code == 404, r.status_code


# --- Regression: startup rater validator log --------------------------------
def test_startup_rater_validation_log():
    out = subprocess.run(
        ["bash", "-lc", "grep -h 'rater validation' /var/log/supervisor/backend.*.log | tail -3"],
        capture_output=True, text=True, timeout=30).stdout
    print(out)
    assert re.search(r"rater validation ok: openrouter/openai/gpt-3\.5-turbo", out), out
