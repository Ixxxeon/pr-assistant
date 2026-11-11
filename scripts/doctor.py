#!/usr/bin/env python3
"""Check the current host, approval hooks, and optionally the review CLI. No writes."""
import json
import os
import sys

from approval import session_path
from reviewer import settings
from reviewers import ReviewError


def main():
    host = os.environ.get("PR_ASSISTANT_HOST")
    sid = os.environ.get("PR_ASSISTANT_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")
    if host not in ("claude", "codex") or not sid:
        sys.exit("pr-assistant: session context is missing. Enable the plugin hooks and start a new session. "
                 "In Codex, review/trust them in /hooks or Settings > Hooks. Do not invent a session id.")
    try:
        session = json.loads(session_path(sid).read_text())
        if not session.get("last_prompt_ts") or session.get("pretool_ts", 0) < session["last_prompt_ts"]:
            raise ValueError("the prompt and pretool hooks have not both run for this turn")
    except (OSError, ValueError) as error:
        sys.exit(f"pr-assistant: approval hooks are not ready ({error}). "
                 "Enable/trust the hooks and retry in a new session; posting is disabled without them.")
    if "--review" in sys.argv:
        try:
            selected = settings()
        except ReviewError as error:
            sys.exit(f"pr-assistant: {error}")
        print(f"host: {host}; reviewer: {selected['engine']}; model: {selected['model'] or 'Codex CLI default'}; hooks: ready")
    else:
        print(f"host: {host}; hooks: ready")


if __name__ == "__main__":
    main()
