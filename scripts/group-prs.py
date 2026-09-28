#!/usr/bin/env python3
"""Group PRs that belong to one task.

Reads PR objects (a JSON array, or one JSON object per line) with repo, number,
title, head_ref and optional body. Two PRs are linked when they share
  - a ticket key (PROJ-1234) in the title or branch name,
  - a branch name that is not a generic one (main, develop, release/...),
  - or one PR's body links to the other PR.
Links are transitive. Prints the PRs as a JSON array with `task` set to the
group label (the most common ticket key, else the branch) for groups of two or
more, null for standalone PRs. `body` is dropped from the output.
"""
import json
import re
import sys
from collections import Counter

TICKET = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d+)\b")
NOT_TICKETS = {"SHA", "UTF", "ISO", "RFC", "CVE", "AES", "TLS", "HTTP", "X25519"}
GENERIC_BRANCH = re.compile(r"^(main|master|develop|dev|staging|release|hotfix)([/-].*)?$", re.IGNORECASE)


def tickets(pr):
    text = f"{pr.get('title') or ''} {pr.get('head_ref') or ''}"
    # branch names often use lowercase keys: proj-1234-...
    found = TICKET.findall(text) + TICKET.findall((pr.get("head_ref") or "").upper())
    return {f"{k}-{n}" for k, n in found if k not in NOT_TICKETS}


def read_prs(text):
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def group(prs):
    parent = list(range(len(prs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[find(a)] = find(b)

    by_key = {}
    per_pr_tickets = []
    for i, pr in enumerate(prs):
        keys = {"ticket:" + t for t in tickets(pr)}
        per_pr_tickets.append(tickets(pr))
        branch = pr.get("head_ref") or ""
        if branch and not GENERIC_BRANCH.match(branch):
            keys.add("branch:" + branch)
        for k in keys:
            if k in by_key:
                union(i, by_key[k])
            else:
                by_key[k] = i
    for i, pr in enumerate(prs):
        body = pr.get("body") or ""
        for j, other in enumerate(prs):
            if i != j and f"github.com/{other['repo']}/pull/{other['number']}" in body:
                union(i, j)

    members = {}
    for i in range(len(prs)):
        members.setdefault(find(i), []).append(i)
    out = []
    for i, pr in enumerate(prs):
        pr = {k: v for k, v in pr.items() if k != "body"}
        grp = members[find(i)]
        if len(grp) < 2:
            pr["task"] = None
        else:
            counts = Counter(t for j in grp for t in per_pr_tickets[j])
            if counts:
                pr["task"] = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            else:
                pr["task"] = prs[grp[0]].get("head_ref") or f"{prs[grp[0]]['repo']}#{prs[grp[0]]['number']}"
        out.append(pr)
    return out


if __name__ == "__main__":
    print(json.dumps(group(read_prs(sys.stdin.read())), ensure_ascii=False, indent=1))
