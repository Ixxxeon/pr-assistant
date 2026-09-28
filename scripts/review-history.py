#!/usr/bin/env python3
"""review-history.py <owner/repo> <number>

What has already been said on a PR you are asked to review, and how to review it now.
Prints JSON:
  me, head_sha,
  reviewed_before: you left a review, a review comment or a PR comment earlier,
  last_reviewed_sha: head commit of your latest submitted review (null if none),
  my_threads: {open_unanswered, open_answered, resolved} — threads you started;
            open_answered (the author replied, not resolved) counts as resolved,
  existing: every review-thread comment on the PR (anyone): path, line, author, body,
            resolved, outdated — used to drop findings that were already raised,
  general: PR-level comments and review bodies (anyone), for the same purpose,
  mode: "full"        — you have not commented yet: normal review,
        "blockers"    — you commented and the PR changed since: review only for
                        confirmed blockers, nothing already discussed,
        "skip"        — you commented and either nothing changed since your last
                        review, or a thread you started still waits for the
                        author's reply (last comment is yours, not resolved),
  reason: one line explaining the mode,
  since_sha: for "blockers", the commit to review changes from (your last reviewed head).
Lists are capped at 100 threads / 100 reviews / 100 comments per PR; `truncated` says if any cap was hit.
"""
import json
import subprocess
import sys

QUERY = """
query($o:String!,$r:String!,$n:Int!){ viewer{login} repository(owner:$o,name:$r){ pullRequest(number:$n){
  headRefOid
  reviews(first:100){ totalCount nodes{ author{login} state submittedAt body commit{oid} } }
  reviewThreads(first:100){ totalCount nodes{ isResolved isOutdated path line originalLine
    comments(first:50){ nodes{ author{login} body createdAt } } } }
  comments(first:100){ totalCount nodes{ author{login} body createdAt } }
}}}"""
CLIP = 400


def login(node):
    return (node.get("author") or {}).get("login", "ghost")


def main():
    repo, number = sys.argv[1], int(sys.argv[2])
    owner, name = repo.split("/", 1)
    out = subprocess.run(["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"o={owner}", "-f", f"r={name}",
                          "-F", f"n={number}"], check=True, capture_output=True, text=True).stdout
    data = json.loads(out)["data"]
    me, pr = data["viewer"]["login"], data["repository"]["pullRequest"]
    head = pr["headRefOid"]

    reviews = [r for r in pr["reviews"]["nodes"] if r["state"] != "PENDING"]
    mine = sorted((r for r in reviews if login(r) == me and r.get("submittedAt")), key=lambda r: r["submittedAt"])
    last_sha = (mine[-1].get("commit") or {}).get("oid") if mine else None

    existing, stats = [], {"open_unanswered": 0, "open_answered": 0, "resolved": 0}
    commented_in_thread = False
    for t in pr["reviewThreads"]["nodes"]:
        comments = t["comments"]["nodes"]
        if not comments:
            continue
        for c in comments:
            commented_in_thread |= login(c) == me
            existing.append({"path": t["path"], "line": t["line"] or t["originalLine"], "author": login(c),
                             "body": c["body"][:CLIP], "resolved": t["isResolved"], "outdated": t["isOutdated"]})
        if login(comments[0]) == me:
            if t["isResolved"]:
                stats["resolved"] += 1
            elif login(comments[-1]) == me:
                stats["open_unanswered"] += 1
            else:
                stats["open_answered"] += 1

    general = [{"author": login(c), "body": c["body"][:CLIP]} for c in pr["comments"]["nodes"] if c["body"].strip()]
    general += [{"author": login(r), "body": r["body"][:CLIP]} for r in reviews if (r.get("body") or "").strip()]
    reviewed_before = bool(mine) or commented_in_thread or any(g["author"] == me for g in general)

    if not reviewed_before:
        mode, reason, since = "full", "first review", None
    elif last_sha == head:
        mode, since, reason = "skip", None, "no new commits since your last review"
    elif stats["open_unanswered"]:
        mode, since = "skip", None
        reason = f"your threads waiting for the author's reply: {stats['open_unanswered']}"
    else:
        mode, since = "blockers", last_sha
        reason = ("changed since your last review" if last_sha else "you commented before") + ": confirmed blockers only"

    truncated = any(pr[k]["totalCount"] > 100 for k in ("reviews", "reviewThreads", "comments"))
    print(json.dumps({"repo": repo, "number": number, "me": me, "head_sha": head, "reviewed_before": reviewed_before,
                      "last_reviewed_sha": last_sha, "my_threads": stats, "mode": mode, "reason": reason,
                      "since_sha": since, "truncated": truncated, "existing": existing, "general": general},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
