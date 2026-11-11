"""One-use approval receipts, issued by the hook and consumed by post.py.

This prevents accidental sends when hooks are missing. Like the original guard,
it is not a security boundary against a process that can edit the plugin/state.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time


def state_dir():
    return Path(os.environ.get("PR_ASSISTANT_STATE_DIR", "~/.cache/pr-assistant")).expanduser()


def session_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return state_dir() / "sessions" / (safe + ".json")


def receipt_path(session_id, path):
    key = hashlib.sha256((session_id + "\n" + str(Path(path).resolve())).encode()).hexdigest()
    return state_dir() / "approvals" / (key + ".json")


def issue(session_id, path, prompt_ts):
    if not session_id:
        raise ValueError("hook did not supply a session id")
    path = Path(path).resolve()
    receipt = {"session_id": session_id, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "mtime_ns": path.stat().st_mtime_ns, "prompt_ts": prompt_ts, "issued": time.time()}
    target = receipt_path(session_id, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=target.parent, delete=False) as tmp:
        json.dump(receipt, tmp)
    os.replace(tmp.name, target)


def consume(path):
    """Read exactly the bytes approved, reject stale/reused approval before any write."""
    sid = os.environ.get("PR_ASSISTANT_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")
    if not sid:
        raise ValueError("missing session id; run through the installed pr-assistant skill and hooks")
    path = Path(path).resolve()
    receipt_file = receipt_path(sid, path)
    try:
        receipt = json.loads(receipt_file.read_text())
        session = json.loads(session_path(sid).read_text())
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        valid = (receipt["session_id"] == sid and receipt["path"] == str(path)
                 and receipt["sha256"] == digest and receipt["mtime_ns"] == path.stat().st_mtime_ns
                 and 0 <= time.time() - receipt["issued"] < 120
                 and receipt["prompt_ts"] == session["last_prompt_ts"]
                 and receipt["prompt_ts"] > path.stat().st_mtime
                 and re.fullmatch(r"\s*ok\s+" + digest[:6] + r"\s*", session["last_prompt"], re.I))
        if not valid:
            raise ValueError("approval is stale or does not match this plan")
        # unlink is the single-consumer claim: concurrent or repeated sends fail.
        receipt_file.unlink()
        return json.loads(body)
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"no valid hook approval for this plan ({error}). "
                         "Enable/trust the plugin hooks, show the plan and obtain ok <CODE> again") from error
