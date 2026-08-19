"""Layer #7 — User Edit Diffing backend tests (iteration 10).

Covers: Chain response_model exposes build_manifest/built_at,
POST /chains/{id}/check-edits (upload zip path, dedup, nested-path
normalization, 400/404/409 edge cases) and GET /chains/{id}/edit-diffs.
"""
import io
import os
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


def _zip(files: dict, prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(f"{prefix}{path}", content)
    return buf.getvalue()


def _files_from_chain(doc: dict) -> dict:
    gen = (doc.get("generations") or [{}])[-1]
    return {f["path"]: f.get("content", "") for f in (gen.get("files") or [])}


# --- Chain response_model exposes manifest fields ---------------------------
class TestChainManifestFields:
    def test_build_manifest_present(self, chain_doc):
        assert chain_doc["status"] == "complete"
        manifest = chain_doc.get("build_manifest")
        assert manifest is not None, "build_manifest stripped from Chain response"
        assert set(manifest.keys()) == {"index.html", "run.sh", "README.md"}, manifest.keys()
        assert all(isinstance(v, str) and len(v) == 64 for v in manifest.values())
        assert chain_doc.get("built_at")


# --- check-edits upload path ------------------------------------------------
class TestCheckEditsUpload:
    def test_modified_index_html_detected(self, client, chain_doc):
        files = _files_from_chain(chain_doc)
        assert "index.html" in files
        marker = "<!-- QA-IT10-MARKER -->"
        files["index.html"] = files["index.html"].replace("<head>", f"<head>\n{marker}", 1)
        assert marker in files["index.html"]
        r = client.post(
            f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits",
            files={"file": ("proj.zip", _zip(files), "application/zip")}, timeout=60)
        assert r.status_code == 200, r.text[:400]
        data = r.json()
        assert data["changed_files"] == 1, data
        d = data["diffs"][0]
        assert d["file_path"] == "index.html"
        assert marker in d["diff_hunk"]
        assert d["diff_hunk"].startswith("---")

    def test_reupload_identical_is_deduped(self, client, chain_doc):
        """Same modified zip again -> hunk identical to last stored, no re-insert."""
        files = _files_from_chain(chain_doc)
        marker = "<!-- QA-IT10-MARKER -->"
        files["index.html"] = files["index.html"].replace("<head>", f"<head>\n{marker}", 1)
        before = client.get(f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/edit-diffs", timeout=30).json()["diffs"]
        r = client.post(
            f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits",
            files={"file": ("proj.zip", _zip(files), "application/zip")}, timeout=60)
        assert r.status_code == 200, r.text[:400]
        # detection still reports the change, but nothing new is persisted
        after = client.get(f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/edit-diffs", timeout=30).json()["diffs"]
        idx_before = [x for x in before if x["file_path"] == "index.html"]
        idx_after = [x for x in after if x["file_path"] == "index.html"]
        assert len(idx_after) == len(idx_before), "duplicate hunk was re-inserted (dedup broken)"

    def test_unmodified_upload_reports_zero(self, client, chain_doc):
        files = _files_from_chain(chain_doc)
        r = client.post(
            f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits",
            files={"file": ("proj.zip", _zip(files), "application/zip")}, timeout=60)
        assert r.status_code == 200, r.text[:400]
        assert r.json()["changed_files"] == 0

    def test_nested_folder_path_normalization(self, client, chain_doc):
        """Files nested under a top-level dir must still match the flat manifest."""
        files = _files_from_chain(chain_doc)
        files["README.md"] = files["README.md"] + "\n\n## QA IT10 nested note\n"
        r = client.post(
            f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits",
            files={"file": ("proj.zip", _zip(files, prefix="myproject/"), "application/zip")},
            timeout=60)
        assert r.status_code == 200, r.text[:400]
        data = r.json()
        assert data["changed_files"] == 1, data
        assert data["diffs"][0]["file_path"] == "README.md"
        assert "QA IT10 nested note" in data["diffs"][0]["diff_hunk"]

    def test_bad_zip_rejected(self, client):
        r = client.post(
            f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits",
            files={"file": ("proj.zip", b"not-a-zip-at-all", "application/zip")}, timeout=30)
        assert r.status_code == 400, r.text[:300]


# --- edge cases -------------------------------------------------------------
class TestCheckEditsEdgeCases:
    def test_no_file_no_github_returns_400(self, client):
        r = client.post(f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/check-edits", timeout=30)
        assert r.status_code == 400, r.text[:300]
        detail = r.json().get("detail", "").lower()
        assert "push" in detail and "zip" in detail, detail

    def test_nonexistent_chain_returns_404(self, client):
        r = client.post(f"{BASE_URL}/api/chains/00000000-0000-0000-0000-000000000000/check-edits",
                        timeout=30)
        assert r.status_code == 404, r.text[:300]

    def test_running_chain_returns_409(self, client):
        r = client.post(f"{BASE_URL}/api/evolve",
                        json={"target_prompt": "TEST_IT10 a tiny hello page", "depth": 1},
                        timeout=60)
        if r.status_code != 200:
            pytest.fail(f"/api/evolve failed {r.status_code}: {r.text[:300]}")
        cid = r.json()["id"]
        assert r.json()["status"] == "running"
        c = client.post(f"{BASE_URL}/api/chains/{cid}/check-edits", timeout=30)
        assert c.status_code == 409, f"expected 409, got {c.status_code} {c.text[:300]}"


# --- persisted history ------------------------------------------------------
class TestEditDiffsHistory:
    def test_history_lists_both_files_and_is_stable(self, client):
        r1 = client.get(f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/edit-diffs", timeout=30)
        assert r1.status_code == 200, r1.text[:300]
        rows = r1.json()["diffs"]
        assert isinstance(rows, list) and rows
        paths = {x["file_path"] for x in rows}
        assert {"index.html", "README.md"} <= paths, paths
        for x in rows:
            for k in ("file_path", "diff_hunk", "source", "detected_at"):
                assert k in x, (k, x)
            assert "seconds_since_build" in x
            assert "_id" not in x
        r2 = client.get(f"{BASE_URL}/api/chains/{FIXTURE_CHAIN}/edit-diffs", timeout=30)
        assert r2.json()["diffs"] == rows, "history not stable across calls"

    def test_edit_diffs_404_for_unknown_chain(self, client):
        r = client.get(f"{BASE_URL}/api/chains/00000000-0000-0000-0000-000000000000/edit-diffs",
                       timeout=30)
        assert r.status_code == 404


# --- light regression smoke -------------------------------------------------
class TestSmoke:
    def test_pricing_tiers(self, client):
        r = client.get(f"{BASE_URL}/api/tiers", timeout=30)
        assert r.status_code in (200, 404), r.status_code
        if r.status_code == 200:
            body = r.json()
            tiers = body if isinstance(body, list) else body.get("tiers", [])
            assert len(tiers) >= 3, tiers

    def test_chains_list(self, client):
        r = client.get(f"{BASE_URL}/api/chains", timeout=30)
        assert r.status_code == 200, r.text[:300]
