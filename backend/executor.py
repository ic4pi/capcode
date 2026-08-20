"""
executor.py — materializes a generation's files and actually RUNS them.

This is the reality check. No human input. No "does it look right." Just:
    bash run.sh → wait 30s → capture stdout/stderr/exit_code + port_listening.
The result is a real signal fed back to the rater and the next gen's planner.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import pwd
import re
import resource
import socket
from typing import Optional

WORKSPACE_ROOT = pathlib.Path(os.environ.get("WORKSPACE_ROOT", "/tmp/capcode_workspaces"))
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)


def _slug(s: str, fallback: str = "gen") -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")
    return s or fallback


def _is_safe_relpath(p: str) -> bool:
    """Shared file-path predicate: reject absolute paths, `..`, backslashes, null bytes."""
    if not p or p.startswith("/") or "\x00" in p or "\\" in p:
        return False
    parts = [seg for seg in p.split("/") if seg]
    if not parts:
        return False
    for seg in parts:
        if seg == ".." or seg == "." or seg.startswith(".."):
            return False
    return True


def materialize(chain_id: str, gen: dict) -> pathlib.Path:
    """Write the gen's files to /workspaces/{chain}/{name} and return the dir.

    SEC-002: reject anything that isn't a safe relative path AND double-check
    it resolves inside ``root``.
    """
    slug = _slug(gen.get("name") or f"gen-{gen.get('gen')}", f"gen-{gen.get('gen')}")
    root = (WORKSPACE_ROOT / chain_id[:8] / slug).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for f in gen.get("files", []):
        rel = (f.get("path") or "").strip().lstrip("/")
        if not _is_safe_relpath(rel):
            continue
        p = (root / rel).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f["content"])
        if p.suffix == ".sh":
            p.chmod(0o755)
    # The child process runs as `nobody` (see _sandbox_preexec), but every
    # file/dir above was just created by root — open up the whole workspace
    # so the generated app can actually write its own logs/db/cache files.
    # Scoped to this one throwaway per-build directory only.
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o777)
        for fn in filenames:
            fp = pathlib.Path(dirpath) / fn
            fp.chmod(0o777 if fp.suffix == ".sh" else 0o666)
    return root


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _sandbox_preexec():
    """Runs in the child, right before exec. Drops root (if we have it) to
    the unprivileged `nobody` account so generated code can't read root-owned
    files like backend/.env (SEC-001) even though it shares this filesystem,
    and caps CPU/memory/open-files so a runaway build can't take the host
    down. Best-effort: if we're already non-root or `nobody` doesn't exist,
    this silently no-ops rather than breaking the run."""
    os.setsid()
    try:
        nobody = pwd.getpwnam("nobody")
        os.setgroups([])
        os.setgid(nobody.pw_gid)
        os.setuid(nobody.pw_uid)
    except (KeyError, PermissionError, OSError):
        pass
    os.umask(0o077)
    for limit, cap in (
        (resource.RLIMIT_CPU, (25, 25)),
        (resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2),
        (resource.RLIMIT_NOFILE, (512, 512)),
    ):
        try:
            resource.setrlimit(limit, cap)
        except (ValueError, OSError):
            pass


async def run_workspace(
    root: pathlib.Path, timeout: int = 30, port_to_check: int = 8000
) -> dict:
    """Execute `bash run.sh` in the workspace, capture logs, verify port opens."""
    result: dict = {
        "ran": False,
        "started": False,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "port_listening": False,
        "duration_s": 0.0,
        "root": str(root),
    }
    run_sh = root / "run.sh"
    if not run_sh.exists():
        result["stderr"] = "no run.sh in workspace"
        return result

    result["ran"] = True
    # SEC-001: build a scrubbed env for the AI-generated child process. Never
    # inherit provider keys, the Mongo URL, GitHub PATs, or anything else that
    # could be exfiltrated by hostile LLM output.
    ALLOWED = ("PATH", "HOME", "TERM", "LANG", "LC_ALL", "LC_CTYPE", "USER",
               "TMPDIR", "PWD", "SHELL")
    env = {k: v for k, v in os.environ.items() if k in ALLOWED}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("HOME", "/tmp")
    # Pin the child app to a unique port so we don't clash with the parent (8001).
    env["APP_PORT"] = str(port_to_check)
    loop = asyncio.get_running_loop()
    start = loop.time()

    proc = await asyncio.create_subprocess_exec(
        "bash", "run.sh",
        cwd=str(root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=_sandbox_preexec,  # drop root -> nobody, cap resources, new pgid
    )

    # Poll port up to `timeout` seconds; when it opens we call it "started".
    for _ in range(timeout * 2):
        if _port_open(port_to_check):
            result["started"] = True
            result["port_listening"] = True
            break
        if proc.returncode is not None:
            break
        await asyncio.sleep(0.5)

    # Give it a beat to emit logs, then tear the whole process group down.
    await asyncio.sleep(0.5)
    import signal as _signal
    pgid = None
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:
        pass
    try:
        if pgid is not None:
            os.killpg(pgid, _signal.SIGTERM)
        else:
            proc.terminate()
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        except asyncio.TimeoutError:
            if pgid is not None:
                try: os.killpg(pgid, _signal.SIGKILL)
                except Exception: pass
            else:
                proc.kill()
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3)
            except asyncio.TimeoutError:
                stdout, stderr = b"", b""
    except ProcessLookupError:
        stdout, stderr = b"", b""

    result["exit_code"] = proc.returncode
    result["stdout"] = (stdout or b"").decode("utf-8", errors="replace")[-4000:]
    result["stderr"] = (stderr or b"").decode("utf-8", errors="replace")[-4000:]
    result["duration_s"] = round(loop.time() - start, 2)
    return result


def brief(exec_result: dict) -> str:
    """Compact one-block summary for feeding into subsequent LLM stages."""
    if not exec_result.get("ran"):
        return "EXEC: skipped (no run.sh)"
    lines = [
        f"EXEC: ran={exec_result['ran']} started={exec_result['started']} "
        f"port_listening={exec_result['port_listening']} "
        f"exit={exec_result['exit_code']} duration={exec_result['duration_s']}s",
    ]
    if exec_result.get("stderr"):
        lines.append("STDERR tail:\n" + exec_result["stderr"][-800:])
    if exec_result.get("stdout"):
        lines.append("STDOUT tail:\n" + exec_result["stdout"][-500:])
    return "\n".join(lines)
