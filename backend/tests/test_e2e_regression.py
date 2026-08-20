"""End-to-end regression: real build (WebGL cube) + SSE delta stream.

Runs a live LLM build; slow by design (~up to 200s).
"""
import os
import re
import subprocess
import threading
import time

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or frontend_env["REACT_APP_BACKEND_URL"]).rstrip("/")

TARGET = "a simple webgl page that draws a black background with a red rotating cube"
SESSION = "regression-test"
HDRS = {"X-Capcode-Session": SESSION}

_state = {}


def _sse_collect(chain_id, seconds, out):
    proc = subprocess.Popen(
        ["curl", "-sN", "-H", "Accept: text/event-stream",
         f"{BASE_URL}/api/chains/{chain_id}/stream?session_id={SESSION}"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    deadline = time.time() + seconds
    try:
        for line in proc.stdout:
            out.append(line.rstrip("\n"))
            if time.time() > deadline:
                break
    finally:
        proc.kill()


class TestEndToEndBuild:
    def test_start_build(self):
        r = requests.post(f"{BASE_URL}/api/evolve",
                          json={"target_prompt": TARGET, "session_id": SESSION},
                          headers=HDRS, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "running"
        assert d["id"]
        assert "_id" not in d
        _state["chain_id"] = d["id"]

    def test_sse_emits_deltas(self):
        cid = _state.get("chain_id")
        if not cid:
            pytest.skip("build not started")
        lines = []
        t = threading.Thread(target=_sse_collect, args=(cid, 25, lines))
        t.start()
        t.join(timeout=40)
        events = [l for l in lines if l.startswith("event:")]
        teacher = [l for l in lines if l.strip() == "event: teacher_delta"]
        artist = [l for l in lines if l.strip() == "event: artist_delta"]
        data_lines = [l for l in lines if l.startswith("data:") and len(l) > 6]
        print(f"SSE events={len(events)} teacher_delta={len(teacher)} "
              f"artist_delta={len(artist)} nonempty_data={len(data_lines)}")
        print("event kinds:", sorted(set(events)))
        assert any("event: open" in l for l in lines), lines[:10]
        assert len(teacher) + len(artist) > 0, f"no _delta events; got {sorted(set(events))}"
        assert len(data_lines) > 0

    def test_chain_completes_with_real_code(self):
        cid = _state.get("chain_id")
        if not cid:
            pytest.skip("build not started")
        deadline = time.time() + 240
        doc = None
        while time.time() < deadline:
            r = requests.get(f"{BASE_URL}/api/chains/{cid}", headers=HDRS, timeout=30)
            assert r.status_code == 200, r.text
            doc = r.json()
            if doc["status"] in ("complete", "failed"):
                break
            time.sleep(6)
        assert doc is not None
        print("final status:", doc["status"], "error:", doc.get("error"))
        assert doc["status"] != "running", "chain stuck in running past 240s"

        if doc["status"] == "failed":
            # acceptable per spec, as long as the error surfaces
            assert doc.get("error"), "failed chain has no error field"
            pytest.skip(f"chain hard-failed (acceptable): {doc.get('error')[:200]}")

        gens = doc.get("generations") or []
        assert gens, "complete chain has no generations"
        g = gens[0]
        files = g.get("files") or []
        assert files, "generation has no files"
        paths = [f["path"] for f in files]
        print("files:", paths)
        assert (g.get("exec") or {}).get("started") is True, g.get("exec")

        html = next((f for f in files if f["path"].endswith(".html")), None)
        assert html, f"no html file among {paths}"
        content = html["content"]
        assert re.search(r"canvas|getContext", content, re.I), content[:400]
        assert re.search(r"webgl|shader|gl_Position|drawArrays|drawElements|three",
                         content, re.I), "html has no WebGL/shader code"
        assert len(content) > 800, f"html looks like boilerplate ({len(content)} chars)"
        _state["complete_chain"] = cid

    def test_download_zip(self):
        cid = _state.get("complete_chain")
        if not cid:
            pytest.skip("no completed chain")
        r = requests.get(f"{BASE_URL}/api/chains/{cid}/download", headers=HDRS, timeout=60)
        assert r.status_code == 200, r.text
        assert r.content[:2] == b"PK", "not a zip"
