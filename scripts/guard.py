#!/usr/bin/env python3
"""pr-assistant approval guard (Claude Code and Codex hooks).

Modes, wired in the host-specific files under hooks/:

  guard.py start   SessionStart: supplies host and session context to the skill.

  guard.py prompt   UserPromptSubmit: records the user's latest prompt and its
                    time per session. Only real user input reaches this hook, so
                    the record cannot be forged by the model.

  guard.py pretool  PreToolUse(Bash):
                    - `post.py <plan>` runs only if the user's latest prompt
                      is exactly `ok <CODE>`, where CODE is derived from the plan
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
from approval import issue

STATE_DIR = os.path.expanduser(os.environ.get("PR_ASSISTANT_STATE_DIR", "~/.cache/pr-assistant"))
SESSIONS_DIR = os.path.join(STATE_DIR, "sessions")
PLUGIN_MARKER = "pr-assistant"  # substring of the plugin's install path and script names
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POST_SCRIPT = os.path.realpath(os.path.join(os.path.dirname(__file__), "post.py"))


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


ATTRIBUTION = re.compile(r"co-authored-by:[^\n]*(claude|anthropic|codex|openai)|generated with \[?(claude|codex)|claude\.com/claude-code",
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
    if "post.py" not in cmd:
        return None
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = []
        for token in lexer:
            if token in ("<<", "<<-"):
                break  # A heredoc's contents are data, not shell argv.
            tokens.append(token)
    except ValueError:
        return None
    invoked = any(os.path.realpath(tok) == POST_SCRIPT and
                  (i == 0 or os.path.basename(tokens[i - 1]) in ("python", "python3"))
                  for i, tok in enumerate(tokens))
    if not invoked:
        return None
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)
    if (len(tokens) == 3 and os.path.basename(tokens[0]) in ("python", "python3")
            and os.path.realpath(tokens[1]) == POST_SCRIPT
            and not re.search(r"[;&|`$\n]", cmd)):
        return tokens[2]
    return ""


def context(event, host):
    sid = event.get("session_id", "")
    if host not in ("claude", "codex") or not sid:
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event.get("hook_event_name", "UserPromptSubmit"),
        "additionalContext": f"For pr-assistant commands only, set PR_ASSISTANT_HOST={shlex.quote(host)} "
        f"and PR_ASSISTANT_SESSION_ID={shlex.quote(sid)} on every shell invocation. "
        f"Plugin root: {root}. reviewer=auto follows this host on every run; do not save a provider "
        "on first use. Use absolute script paths when calling post.py alone."
    }}))


def mode_prompt(event, host):
    sid = event.get("session_id", "")
    data = load_session(sid)
    prompt = event.get("prompt", "") or ""
    data["last_prompt"] = prompt
    data["last_prompt_ts"] = time.time()
    if PLUGIN_MARKER in prompt:
        data["active"] = True
    save_session(sid, data)
    if data.get("active"):
        context(event, host)
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
        approved = re.fullmatch(r"\s*ok\s+" + code + r"\s*", prompt, re.IGNORECASE)
        fresh = data.get("last_prompt_ts", 0) > os.path.getmtime(plan)
        if approved and fresh:
            try:
                issue(sid, plan, data["last_prompt_ts"])
            except (OSError, ValueError) as error:
                decide("deny", f"pr-assistant: could not record approval: {error}")
            decide("allow", f"pr-assistant: plan {code} approved by the user.")
        decide("deny",
               f"pr-assistant: plan not approved. Show the user the final drafts and ask them to reply "
               f"`ok {code}` to send exactly this plan. Do not post any other way.")

    if PLUGIN_MARKER in cmd or PLUGIN_ROOT in cmd:
        if not data.get("active"):
            data["active"] = True
        data["pretool_ts"] = time.time()
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
    if os.environ.get("PR_ASSISTANT_REVIEW_WORKER") == "1":
        return
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    host = sys.argv[2] if len(sys.argv) > 2 else "claude"
    try:
        event = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    if mode == "start":
        context(dict(event, hook_event_name="SessionStart"), host)
    elif mode == "prompt":
        mode_prompt(event, host)
    elif mode == "pretool":
        mode_pretool(event)
    sys.exit(0)


if __name__ == "__main__":
    main()
