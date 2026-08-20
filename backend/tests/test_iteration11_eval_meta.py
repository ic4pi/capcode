"""Layer #5 — Evaluation-Criteria Meta-Loop backend tests (iteration 11).

Covers: GET /api/eval-weights (public defaults), opportunistic
maybe_recompute() wiring on /verify and /check-edits (never surfaced as an
error, throttled), composite_v2() weight sensitivity (direct import), and
the Layer #7 regression fix (seconds_since_build in the check-edits
response itself).
"""
import io
import os
import sys
import time
import zipfile

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

FIXTURE_CHAIN = "6d9a44c9-9060-477a-9d54-cdae50125b24"
FIXTURE_SESSION = "selftest-edits-1787072853"
HEADERS = {"X-Capcode-Session": FIXTURE_SESSION}

sys.path.insert(0, "/app/backend")


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


@pytest.fixture(scope="session")
def chain_doc(client):
    r = client.get(f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}", timeout=30)
    assert r.status_code == 200, r.text[:400]
    return r.json()


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _files_from_chain(doc: dict) -> dict:
    gen = (doc.get("generations") or [{}])[-1]
    return {f["path"]: f.get("content", "") for f in (gen.get("files") or [])}


# --- GET /api/eval-weights (public, defaults on low sample size) ------------
class TestEvalWeightsEndpoint:
    def test_public_no_auth_headers(self):
        """No X-Capcode-Session / Authorization at all."""
        r = requests.get(f"{BASE_URL}/api/eval-weights", timeout=30)
        assert r.status_code == 200, r.text[:400]
        data = r.json()
        assert set(data.keys()) >= {"weights", "sample_size", "updated_at", "is_default"}, data
        assert data["weights"] == {"coverage": 0.4, "rater": 0.4, "novelty": 0.2}, data["weights"]
        assert data["is_default"] is True, data
        assert data["updated_at"] is None, data
        assert isinstance(data["sample_size"], int)

    def test_weights_sum_to_one(self):
        w = requests.get(f"{BASE_URL}/api/eval-weights", timeout=30).json()["weights"]
        assert abs(sum(w.values()) - 1.0) < 1e-6, w

    def test_no_mongo_id_leak(self):
        body = requests.get(f"{BASE_URL}/api/eval-weights", timeout=30).text
        assert "_id" not in body, body[:300]

    def test_idempotent_repeat_reads(self):
        first = requests.get(f"{BASE_URL}/api/eval-weights", timeout=30).json()
        second = requests.get(f"{BASE_URL}/api/eval-weights", timeout=30).json()
        assert first["weights"] == second["weights"]
        assert first["is_default"] == second["is_default"]


# --- /verify and /check-edits opportunistic recompute (must never error) ----
class TestRecomputeWiringNeverBreaksRequests:
    def test_verify_repeated_rapid_calls_ok(self, client):
        for i in range(3):
            r = client.post(f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/verify", timeout=60)
            assert r.status_code == 200, f"call {i}: {r.status_code} {r.text[:300]}"
            body = r.json()
            assert body["verified"] is True
            assert body["id"] == FIXTURE_CHAIN

    def test_eval_weights_still_default_after_verify(self):
        """Below MIN_SAMPLE_SIZE the recompute must record checked_at only —
        the weights themselves must stay the untouched defaults."""
        data = requests.get(f"{BASE_URL}/api/eval-weights", timeout=30).json()
        assert data["weights"] == {"coverage": 0.4, "rater": 0.4, "novelty": 0.2}, data
        assert data["is_default"] is True, data
        assert data["updated_at"] is None, data

    def test_check_edits_with_changes_ok_and_no_error(self, client, chain_doc):
        files = _files_from_chain(chain_doc)
        assert files, "fixture chain has no files"
        target = "index.html" if "index.html" in files else sorted(files)[0]
        marked = dict(files)
        marked[target] = files[target] + f"\n<!-- IT11 recompute probe {time.time()} -->\n"
        r = client.post(
            f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits",
            files={"file": ("edited.zip", _zip(marked), "application/zip")}, timeout=90,
        )
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert body["changed_files"] >= 1, body
        assert body["chain_id"] == FIXTURE_CHAIN

    def test_check_edits_no_changes_still_200(self, client, chain_doc):
        """changed_files == 0 path (recompute NOT called) must behave normally."""
        r = client.post(
            f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits",
            files={"file": ("same.zip", _zip(_files_from_chain(chain_doc)), "application/zip")},
            timeout=90,
        )
        assert r.status_code == 200, r.text[:400]
        assert r.json()["changed_files"] == 0, r.json()


# --- Layer #7 regression: seconds_since_build in the SAME response ---------
class TestSecondsSinceBuildInResponse:
    def test_fresh_diff_response_includes_seconds_since_build(self, client, chain_doc):
        files = _files_from_chain(chain_doc)
        target = "README.md" if "README.md" in files else sorted(files)[0]
        marked = dict(files)
        marked[target] = files[target] + f"\n<!-- IT11 unique edit {time.time()} -->\n"
        r = client.post(
            f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits",
            files={"file": ("edited.zip", _zip(marked), "application/zip")}, timeout=90,
        )
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert body["changed_files"] >= 1, body
        entry = next(d for d in body["diffs"] if d["file_path"] == target)
        assert "seconds_since_build" in entry, entry.keys()
        assert entry["seconds_since_build"] is not None, entry
        assert isinstance(entry["seconds_since_build"], int)
        assert entry["seconds_since_build"] > 0, entry
        assert entry["diff_hunk"], entry

    def test_persisted_row_matches_response_value(self, client, chain_doc):
        files = _files_from_chain(chain_doc)
        target = "index.html" if "index.html" in files else sorted(files)[0]
        marker = f"<!-- IT11 persist check {time.time()} -->"
        marked = dict(files)
        marked[target] = files[target] + f"\n{marker}\n"
        r = client.post(
            f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits",
            files={"file": ("edited.zip", _zip(marked), "application/zip")}, timeout=90,
        )
        entry = next(d for d in r.json()["diffs"] if d["file_path"] == target)
        rows = client.get(f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/edit-diffs", timeout=30).json()["diffs"]
        row = next(x for x in rows if x["file_path"] == target and marker in (x.get("diff_hunk") or ""))
        assert row["seconds_since_build"] == entry["seconds_since_build"], (row, entry)


# --- composite_v2 weight sensitivity (direct import, unit level) -----------
class TestCompositeV2Weights:
    def _args(self):
        return dict(
            coverage=0.8,
            rater_scores={"helpfulness": 3.0, "correctness": 3.5, "coherence": 2.5,
                          "complexity": 2.0, "verbosity": 2.0},
            novelty=0.2,
        )

    def test_default_matches_explicit_original_weights(self):
        import chain_generator as cg
        a = self._args()
        default = cg.composite_v2(a["coverage"], a["rater_scores"], a["novelty"], weights=None)
        explicit = cg.composite_v2(a["coverage"], a["rater_scores"], a["novelty"],
                                   weights={"coverage": 0.4, "rater": 0.4, "novelty": 0.2})
        assert default["score"] == explicit["score"], (default, explicit)
        assert default["gated"] is False

    def test_custom_weights_change_score(self):
        import chain_generator as cg
        a = self._args()
        default = cg.composite_v2(a["coverage"], a["rater_scores"], a["novelty"], weights=None)
        custom = cg.composite_v2(a["coverage"], a["rater_scores"], a["novelty"],
                                 weights={"coverage": 0.6, "rater": 0.2, "novelty": 0.2})
        assert custom["score"] != default["score"], (custom, default)
        # coverage(0.8) > rater_norm here, so shifting weight to coverage must raise it
        assert custom["score"] > default["score"], (custom, default)

    def test_coverage_gate_ignores_weights(self):
        import chain_generator as cg
        a = self._args()
        gated = cg.composite_v2(0.1, a["rater_scores"], 1.0,
                                weights={"coverage": 0.1, "rater": 0.1, "novelty": 0.8})
        assert gated["gated"] is True and gated["score"] == 0.0, gated

    def test_eval_meta_module_constants(self):
        import eval_meta
        assert eval_meta.DEFAULT_WEIGHTS == {"coverage": 0.4, "rater": 0.4, "novelty": 0.2}
        assert eval_meta.MIN_SAMPLE_SIZE == 15
        assert eval_meta.RECOMPUTE_THROTTLE_SEC == 3600

    def test_new_progress_carries_eval_weights(self):
        import chain_generator as cg
        w = {"coverage": 0.5, "rater": 0.3, "novelty": 0.2}
        p = cg.new_progress("test-id", "TEST_ prompt", "paid", eval_weights=w)
        assert p["eval_weights"] == w
        p2 = cg.new_progress("test-id", "TEST_ prompt", "free")
        assert p2["eval_weights"] is None


# --- light regression smoke -------------------------------------------------
class TestSmoke:
    @pytest.mark.parametrize("path", ["/api/tier/prices", "/api/tier/status", "/api/chains"])
    def test_endpoints_ok(self, client, path):
        r = client.get(f"{BASE_URL}{path}", timeout=30)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
