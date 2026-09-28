#!/usr/bin/env python3
"""plan.py < plan.json  — register a plan of GitHub writes and print its approval code.

Input (JSON on stdin):
{
  "kind": "review" | "followup",
  "lang": "en" | "ru",               # language of the preview shown to the user (default en)
  "reviews": [                       # review comments on someone else's PR (one GitHub review per PR)
    {"repo": "Owner/name", "number": 12, "commit_id": "<head sha>",
     "comments": [{"n": 1, "path": "src/A.java", "line": 42, "body": "..."}],   # inline, RIGHT side
     "general":  [{"n": 2, "body": "..."}]}                                       # no line anchor
  ],
  "prs": [                           # follow-up on your own PR: all of it goes out as one batch per PR
    {"repo": "Owner/name", "number": 7, "pr_node_id": "PR_kw...",
     "push": {"worktree": "/abs/path", "remote_branch": "feature/x"},        # optional, fast-forward only
     "replies": [{"n": 3, "thread_id": "PRRT_kw...", "body": "Done"}],      # thread replies, one review
     "general": [{"n": 4, "body": "..."}],                                   # review body (answers to top-level comments)
     "rerequest": ["login1", "login2"]}                                      # re-request review from them
  ]
}

The plan is written to $STATE_DIR/plans/. Its approval code is derived from the
file content; post.py runs only after the user types `ok <CODE>` (guard.py).
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

STATE_DIR = os.path.expanduser(os.environ.get("PR_ASSISTANT_STATE_DIR", "~/.cache/pr-assistant"))


def fail(msg):
    print(f"pr-assistant: {msg}", file=sys.stderr)
    sys.exit(1)


def validate(p):
    if p.get("kind") not in ("review", "followup"):
        fail("kind must be review or followup")
    for r in p.get("reviews", []):
        for k in ("repo", "number", "commit_id"):
            if not r.get(k):
                fail(f"review entry needs {k}")
        for c in r.get("comments", []):
            if not (c.get("path") and isinstance(c.get("line"), int) and c.get("body", "").strip()):
                fail(f"inline comment needs path, integer line and body: {c}")
        for g in r.get("general", []):
            if not g.get("body", "").strip():
                fail("general comment needs a body")
    for pr in p.get("prs", []):
        for k in ("repo", "number", "pr_node_id"):
            if not pr.get(k):
                fail(f"prs entry needs {k}")
        push = pr.get("push")
        if push:
            if not (push.get("worktree") and push.get("remote_branch")):
                fail("push needs worktree and remote_branch")
            if not os.path.isdir(push["worktree"]):
                fail(f"no worktree {push['worktree']}")
        for rp in pr.get("replies", []):
            if not (rp.get("thread_id") and rp.get("body", "").strip()):
                fail(f"reply needs thread_id and body: {rp}")
        for g in pr.get("general", []):
            if not g.get("body", "").strip():
                fail("general needs a body")
        if not all(isinstance(x, str) and x for x in pr.get("rerequest", [])):
            fail("rerequest must be a list of logins")
        if not (push or pr.get("replies") or pr.get("general") or pr.get("rerequest")):
            fail(f"nothing to do for {pr['repo']}#{pr['number']}")
    if not (p.get("reviews") or p.get("prs")):
        fail("empty plan")


TEXT = {
    "en": {"general": "(general comment)", "no_commits": "(no new commits)", "one_review": "as one review:",
           "to_thread": "to thread", "to_body": "review body", "rerequest": "re-request review",
           "approve": "To send exactly this, reply: ok {code}"},
    "ru": {"general": "(общий комментарий)", "no_commits": "(нет новых коммитов)", "one_review": "одним ревью:",
           "to_thread": "в тред", "to_body": "в тело ревью", "rerequest": "перезапросить ревью",
           "approve": "Чтобы отправить ровно это, ответь: ok {code}"},
}


def preview(p):
    t = TEXT.get(p.get("lang"), TEXT["en"])
    lines = []
    for r in p.get("reviews", []):
        lines.append(f"\n{r['repo']}#{r['number']}  https://github.com/{r['repo']}/pull/{r['number']}")
        for c in r.get("comments", []):
            lines.append(f"  [{c.get('n', '-')}] {c['path']}:{c['line']}\n      {c['body']}")
        for g in r.get("general", []):
            lines.append(f"  [{g.get('n', '-')}] {t['general']}\n      {g['body']}")
    for pr in p.get("prs", []):
        lines.append(f"\n{pr['repo']}#{pr['number']}  https://github.com/{pr['repo']}/pull/{pr['number']}")
        push = pr.get("push")
        if push:
            log = subprocess.run(["git", "-C", push["worktree"], "log", "--format=      %h %s",
                                  f"origin/{push['remote_branch']}..HEAD"], capture_output=True, text=True).stdout.rstrip()
            lines.append(f"  push → {push['remote_branch']}\n{log or '      ' + t['no_commits']}")
        items = pr.get("replies", []) + pr.get("general", [])
        if items:
            lines.append(f"  {t['one_review']}")
            for rp in pr.get("replies", []):
                lines.append(f"    [{rp.get('n', '-')}] {t['to_thread']}: {rp['body']}")
            for g in pr.get("general", []):
                lines.append(f"    [{g.get('n', '-')}] {t['to_body']}: {g['body']}")
        if pr.get("rerequest"):
            lines.append(f"  {t['rerequest']}: {', '.join(pr['rerequest'])}")
    return "\n".join(lines)


def main():
    try:
        plan = json.load(sys.stdin)
    except ValueError as e:
        fail(f"plan is not valid JSON: {e}")
    validate(plan)
    plan["created"] = datetime.datetime.now().isoformat(timespec="seconds")
    body = json.dumps(plan, ensure_ascii=False, indent=1, sort_keys=True).encode()
    code = hashlib.sha256(body).hexdigest()[:6].upper()
    os.makedirs(os.path.join(STATE_DIR, "plans"), exist_ok=True)
    path = os.path.join(STATE_DIR, "plans", f"plan-{datetime.datetime.now():%Y%m%d-%H%M%S}-{code}.json")
    with open(path, "wb") as f:
        f.write(body)
    print(preview(plan))
    print(f"\nPLAN_FILE={path}\nAPPROVAL_CODE={code}")
    print(TEXT.get(plan.get("lang"), TEXT["en"])["approve"].format(code=code))


if __name__ == "__main__":
    main()
