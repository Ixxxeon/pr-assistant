#!/usr/bin/env python3
"""pr-assistant approval guard (Claude Code hook).

Two modes, wired in hooks/hooks.json:

  guard.py prompt   UserPromptSubmit: records the user's latest prompt and its
                    time per session. Only real user input reaches this hook, so
                    the record cannot be forged by the model.

  guard.py pretool  PreToolUse(Bash):
                    - `post.py <plan>` runs only if the user's latest prompt
                      contains `ok <CODE>`, where CODE is derived from the plan
                      file's content, and that prompt came after the plan was
                      written. Editing the plan after approval changes the code.
                    - once a session has touched pr-assistant scripts, `git commit`
                      with a Co-Authored-By / "Generated with Claude" line is
                      denied, and direct
                      GitHub writes (gh pr comment/review/merge, gh api
                      POST/PATCH/PUT/DELETE, GraphQL mutations, git push) are
                      denied: every write must go through an approved plan.
                    Everything else passes untouched, so other sessions are not
                    affected.

A "deny" decision blocks the tool even in bypassPermissions mode.
"""
import hashlib
import json
import os
import re
import shlex
import sys
import time

STATE_DIR = os.path.expanduser(os.environ.get("PR_ASSISTANT_STATE_DIR", "~/.cache/pr-assistant"))
SESSIONS_DIR = os.path.join(STATE_DIR, "sessions")
PLUGIN_MARKER = "pr-assistant"  # substring of the plugin's install path and script names


def plan_code(path):
    """Approval code for a plan: first 6 hex chars of its content hash."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:6].upper()


def session_file(session_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return os.path.join(SESSIONS_DIR, safe + ".json")


def load_session(session_id):
    try:
        with open(session_file(session_id)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_session(session_id, data):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    tmp = session_file(session_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, session_file(session_id))


def decide(decision, reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


GITHUB_WRITE_PATTERNS = [
    r"\bgit\b[^|;&]*\bpush\b",
    r"\bgh\s+pr\s+(comment|review|merge|close|edit|ready|reopen|create)\b",
    r"\bgh\s+issue\s+(comment|close|edit|create|reopen)\b",
    r"\bgh\s+api\b[^|;&]*(-X|--method)[\s=]*['\"]?(POST|PUT|PATCH|DELETE)",
    r"\bgh\s+api\s+graphql\b[^|;&]*\bmutation\b",
    r"\bcurl\b[^|;&]*api\.github\.com[^|;&]*(-X\s*(POST|PUT|PATCH|DELETE)|--data|-d\s)",
]


ATTRIBUTION = re.compile(r"co-authored-by:[^\n]*(claude|anthropic)|generated with \[?claude|claude\.com/claude-code",
                         re.IGNORECASE)


def is_attributed_commit(cmd):
    """git commit whose message (inline or from a heredoc in the same command) credits the assistant."""
    return bool(re.search(r"\bgit\b[^|;&]*\bcommit\b", cmd)) and bool(ATTRIBUTION.search(cmd))


def is_github_write(cmd):
    if any(re.search(p, cmd, re.IGNORECASE) for p in GITHUB_WRITE_PATTERNS):
        return True
    # `gh api` with request fields defaults to POST unless GET is forced.
    for m in re.finditer(r"\bgh\s+api\b([^|;&]*)", cmd):
        args = m.group(1)
        if re.search(r"(^|\s)(-f|-F|--field|--raw-field|--input)(\s|=)", args) \
                and not re.search(r"(-X|--method)[\s=]*['\"]?GET", args, re.IGNORECASE) \
                and not re.match(r"\s*graphql\b", args):
            return True
    return False


def post_plan_arg(cmd):
    """Plan path if the command runs pr-assistant's post.py, else None."""
    if "post.py" not in cmd or PLUGIN_MARKER not in cmd:
        return None
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return ""
    for i, tok in enumerate(tokens):
        if tok.endswith("post.py"):
            rest = [t for t in tokens[i + 1:] if not t.startswith("-")]
            return rest[0] if rest else ""
    return ""


def mode_prompt(event):
    sid = event.get("session_id", "")
    data = load_session(sid)
    prompt = event.get("prompt", "") or ""
    data["last_prompt"] = prompt
    data["last_prompt_ts"] = time.time()
    if PLUGIN_MARKER in prompt:
        data["active"] = True
    save_session(sid, data)
    sys.exit(0)


def mode_pretool(event):
    if event.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (event.get("tool_input") or {}).get("command", "") or ""
    sid = event.get("session_id", "")
    data = load_session(sid)

    plan = post_plan_arg(cmd)
    if plan is not None:
        data["active"] = True
        save_session(sid, data)
        if not plan or not os.path.isfile(plan):
            decide("deny", "pr-assistant: post.py needs an existing plan file path as its first argument.")
        if re.search(r"[;&|`$]", cmd.split("post.py", 1)[1]):
            decide("deny", "pr-assistant: run post.py alone, without chained or substituted commands.")
        code = plan_code(plan)
        prompt = data.get("last_prompt", "") or ""
        approved = re.search(r"\bok\s+" + code + r"\b", prompt, re.IGNORECASE)
        fresh = data.get("last_prompt_ts", 0) > os.path.getmtime(plan)
        if approved and fresh:
            decide("allow", f"pr-assistant: plan {code} approved by the user.")
        decide("deny",
               f"pr-assistant: plan not approved. Show the user the final drafts and ask them to reply "
               f"`ok {code}` to send exactly this plan. Do not post any other way.")

    if PLUGIN_MARKER in cmd:
        if not data.get("active"):
            data["active"] = True
            save_session(sid, data)

    if data.get("active") and is_attributed_commit(cmd):
        decide("deny",
               "pr-assistant: commit messages must not credit the assistant (no Co-Authored-By or "
               "\"Generated with\" lines). Commit again with only the description of the change.")
    if data.get("active") and is_github_write(cmd):
        decide("deny",
               "pr-assistant: direct GitHub writes are blocked in this session. Put the action into a plan "
               "(plan.py) and send it with post.py after the user types `ok <CODE>`.")
    sys.exit(0)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    if mode == "prompt":
        mode_prompt(event)
    elif mode == "pretool":
        mode_pretool(event)
    sys.exit(0)


if __name__ == "__main__":
    main()
