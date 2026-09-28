#!/usr/bin/env python3
"""pr-status.py <owner/repo> <number>

Status of one of your PRs as JSON:
  - approvals / changes requested (latest opinionated review per reviewer),
  - ready_to_merge: >= 2 approvals, nothing requesting changes, not a draft,
  - threads_needing_reply: unresolved review threads whose last comment is not yours,
  - comments_needing_reply: top-level PR comments by others after your last one,
  - reviewers: humans who reviewed (latest state), suggested_rerequest: reviewers
    who wait on you (changes requested or an unanswered thread),
  - pending_review: true if you already have an unsubmitted review on this PR.
"""
import json
import subprocess
import sys

QUERY = """
query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){ pullRequest(number:$n){
  id url title isDraft mergeable reviewDecision headRefName baseRefName headRefOid
  latestReviews(first:50){ nodes{ author{login __typename} state } }
  reviews(first:1, states:[PENDING]){ totalCount }
  latestOpinionatedReviews(first:50){ nodes{ author{login} state submittedAt } }
  reviewThreads(first:100){ nodes{ id isResolved isOutdated path line originalLine
    comments(first:50){ nodes{ databaseId author{login __typename} body createdAt url } } } }
  comments(last:50){ nodes{ databaseId author{login __typename} body createdAt url } }
}}}"""


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main():
    repo, number = sys.argv[1], int(sys.argv[2])
    owner, name = repo.split("/", 1)
    me = gh("api", "user", "--jq", ".login").strip()
    data = json.loads(gh("api", "graphql", "-f", f"query={QUERY}", "-f", f"o={owner}", "-f", f"r={name}", "-F", f"n={number}"))
    pr = data["data"]["repository"]["pullRequest"]

    reviews = pr["latestOpinionatedReviews"]["nodes"]
    approvals = sorted({r["author"]["login"] for r in reviews if r["state"] == "APPROVED" and r["author"]})
    changes = sorted({r["author"]["login"] for r in reviews if r["state"] == "CHANGES_REQUESTED" and r["author"]})

    threads = []
    for t in pr["reviewThreads"]["nodes"]:
        comments = t["comments"]["nodes"]
        if t["isResolved"] or not comments:
            continue
        last = comments[-1]
        if last["author"] and last["author"]["login"] == me:
            continue
        threads.append({
            "thread_id": t["id"],
            "reply_to_comment_id": comments[0]["databaseId"],
            "path": t["path"],
            "line": t["line"] or t["originalLine"],
            "outdated": t["isOutdated"],
            "url": last["url"],
            "conversation": [{"author": c["author"]["login"] if c["author"] else "ghost",
                              "bot": bool(c["author"]) and c["author"]["__typename"] == "Bot",
                              "body": c["body"]} for c in comments],
        })

    top = pr["comments"]["nodes"]
    last_mine = max((i for i, c in enumerate(top) if c["author"] and c["author"]["login"] == me), default=-1)
    issue_comments = [{"comment_id": c["databaseId"], "author": c["author"]["login"] if c["author"] else "ghost",
                       "bot": bool(c["author"]) and c["author"]["__typename"] == "Bot",
                       "body": c["body"], "url": c["url"]}
                      for c in top[last_mine + 1:] if not (c["author"] and c["author"]["login"] == me)]

    reviewers = {}
    for r in pr["latestReviews"]["nodes"]:
        a = r["author"]
        if a and a["login"] != me and a["__typename"] != "Bot":
            reviewers[a["login"]] = r["state"]
    waiting = set(changes)
    for t in threads:
        waiting |= {c["author"] for c in t["conversation"] if not c["bot"] and c["author"] not in (me, "ghost")}
    waiting |= {c["author"] for c in issue_comments if not c["bot"] and c["author"] not in (me, "ghost")}

    print(json.dumps({
        "repo": repo, "number": number, "pr_node_id": pr["id"], "url": pr["url"], "title": pr["title"], "draft": pr["isDraft"],
        "head_ref": pr["headRefName"], "base_ref": pr["baseRefName"], "head_sha": pr["headRefOid"],
        "mergeable": pr["mergeable"], "review_decision": pr["reviewDecision"],
        "approvals": approvals, "changes_requested": changes,
        "ready_to_merge": len(approvals) >= 2 and not changes and not pr["isDraft"],
        "reviewers": reviewers, "suggested_rerequest": sorted(waiting),
        "pending_review": pr["reviews"]["totalCount"] > 0,
        "threads_needing_reply": threads, "comments_needing_reply": issue_comments,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
