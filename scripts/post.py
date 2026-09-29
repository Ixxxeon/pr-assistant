#!/usr/bin/env python3
"""post.py <plan-file>  — execute an approved plan (see plan.py).

guard.py lets this run only after the user typed `ok <CODE>` for this exact
plan file. A plan is sent once: a `.sent` marker blocks re-sending.

Review plans ("reviews"): one COMMENT review per PR (never request changes).
If GitHub rejects the inline anchors, comments are retried one by one and the
rejected ones go into a general comment with their file:line. An entry with
"approve": true (and no comments) is posted as an APPROVE review, only if the
PR head is still the reviewed commit_id; otherwise it is skipped.

Follow-up plans ("prs"), per PR, in this order:
  1. push the worktree branch (fast-forward only, never --force), then
     fast-forward the user's local branch of the same name if it has nothing
     of its own (git refuses if that would overwrite uncommitted changes);
  2. all thread replies + general text as ONE submitted COMMENT review
     (a pending review, replies added to it, then submitted — one notification);
  3. re-request review from the listed reviewers (the UI "Re-request review").
If the push fails, steps 2-3 for that PR are skipped so "Done" never goes
out without the fix. If step 2 fails midway, the pending review is deleted.
"""
import json
import os
import subprocess
import sys
import tempfile


def gh_api(method, path, payload):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(payload, f, ensure_ascii=False)
        tmp = f.name
    try:
        r = subprocess.run(["gh", "api", "-X", method, path, "--input", tmp], capture_output=True, text=True)
    finally:
        os.unlink(tmp)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout).strip()
    try:
        return json.loads(r.stdout or "{}"), None
    except ValueError:
        return {}, None


def gql(query, **variables):
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        args += ["-f", f"{k}={v}"]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout).strip()
    d = json.loads(r.stdout)
    if d.get("errors"):
        return None, json.dumps(d["errors"], ensure_ascii=False)
    return d["data"], None


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.returncode, (r.stdout.strip() if r.returncode == 0 else (r.stderr or r.stdout).strip())


def sync_local_branch(wt, branch, tag, log):
    """After a push, fast-forward the user's local <branch> in the same clone to the
    pushed commit, so no pull is needed. Only a fast-forward; a diverged branch or a
    checkout git refuses to update is left as is and reported."""
    _, new = git("-C", wt, "rev-parse", "HEAD")
    rc, old = git("-C", wt, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
    if rc != 0 or old == new:
        return
    if git("-C", wt, "merge-base", "--is-ancestor", old, new)[0] != 0:
        log.append(f"  local {branch} ({tag}) has commits not on origin; not updated")
        return
    _, listing = git("-C", wt, "worktree", "list", "--porcelain")
    checkout = None
    for block in listing.split("\n\n"):
        lines = block.splitlines()
        if f"branch refs/heads/{branch}" in lines:
            checkout = lines[0].split(" ", 1)[1]
    if checkout is None:
        rc, err = git("-C", wt, "update-ref", f"refs/heads/{branch}", new, old)
    else:
        # git refuses the fast-forward if it would overwrite uncommitted changes
        rc, err = git("-C", checkout, "merge", "--ff-only", "-q", new)
    if rc == 0:
        log.append(f"  local {branch}: fast-forwarded to {new[:10]}" + (f" in {checkout}" if checkout else ""))
    else:
        log.append(f"  local {branch} ({tag}): not updated ({err.splitlines()[0][:200] if err else 'git failed'}); run git pull in {checkout or 'the clone'}")


def followup(pr, log):
    repo, n = pr["repo"], pr["number"]
    tag = f"{repo}#{n}"
    push = pr.get("push")
    if push:
        r = subprocess.run(["git", "-C", push["worktree"], "push", "origin", f"HEAD:refs/heads/{push['remote_branch']}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            log.append(f"push {tag}: FAILED {r.stderr.strip()[:300]}\n  replies and re-request for {tag} skipped")
            return
        log.append(f"push {tag} → {push['remote_branch']}: ok")
        sync_local_branch(push["worktree"], push["remote_branch"], tag, log)

    replies, general = pr.get("replies", []), pr.get("general", [])
    if replies or general:
        body = "\n\n".join(g["body"] for g in general)
        data, err = gql("mutation($pr:ID!){addPullRequestReview(input:{pullRequestId:$pr}){pullRequestReview{id}}}",
                        pr=pr["pr_node_id"])
        if data is None:
            log.append(f"review {tag}: FAILED to start ({err[:200]}). If you have an unsubmitted review on this PR, "
                       f"submit or discard it in the GitHub UI first.")
            return
        rid = data["addPullRequestReview"]["pullRequestReview"]["id"]
        failed = None
        for rp in replies:
            _, err = gql("mutation($r:ID!,$t:ID!,$b:String!){addPullRequestReviewThreadReply(input:"
                         "{pullRequestReviewId:$r,pullRequestReviewThreadId:$t,body:$b}){comment{id}}}",
                         r=rid, t=rp["thread_id"], b=rp["body"])
            if err:
                failed = f"reply [{rp.get('n', '-')}] {err[:200]}"
                break
        if failed is None:
            data, err = gql("mutation($r:ID!,$b:String){submitPullRequestReview(input:"
                            "{pullRequestReviewId:$r,event:COMMENT,body:$b}){pullRequestReview{url}}}", r=rid, b=body)
            if data is None:
                failed = f"submit {err[:200]}"
            else:
                log.append(f"review {tag}: {len(replies)} replies in one review — "
                           f"{data['submitPullRequestReview']['pullRequestReview']['url']}")
        if failed:
            gql("mutation($r:ID!){deletePullRequestReview(input:{pullRequestReviewId:$r}){clientMutationId}}", r=rid)
            log.append(f"review {tag}: FAILED ({failed}); pending review discarded, nothing posted; re-request skipped")
            return

    if pr.get("rerequest"):
        res, err = gh_api("POST", f"repos/{repo}/pulls/{n}/requested_reviewers", {"reviewers": pr["rerequest"]})
        log.append(f"re-request {tag}: " + (", ".join(pr["rerequest"]) if res is not None else f"FAILED {err[:200]}"))


def approve(r, log):
    repo, n = r["repo"], r["number"]
    tag = f"{repo}#{n}"
    p = subprocess.run(["gh", "api", f"repos/{repo}/pulls/{n}", "--jq", ".head.sha"], capture_output=True, text=True)
    head = p.stdout.strip()
    if p.returncode != 0 or not head:
        log.append(f"approve {tag}: FAILED to read the PR head ({(p.stderr or p.stdout).strip()[:200]})")
        return
    if head != r["commit_id"]:
        log.append(f"approve {tag}: skipped, new commits since the review ({r['commit_id'][:10]} → {head[:10]})")
        return
    res, err = gh_api("POST", f"repos/{repo}/pulls/{n}/reviews", {"commit_id": head, "event": "APPROVE", "body": ""})
    log.append(f"approve {tag}: " + (res.get("html_url", "ok") if res is not None else f"FAILED {err[:200]}"))


def post_review(r, log):
    repo, n = r["repo"], r["number"]
    if r.get("approve"):
        approve(r, log)
        return
    base = f"repos/{repo}/pulls/{n}/reviews"
    inline = [{"path": c["path"], "line": c["line"], "side": "RIGHT", "body": c["body"]} for c in r.get("comments", [])]
    general = [g["body"] for g in r.get("general", [])]
    if inline:
        res, err = gh_api("POST", base, {"commit_id": r["commit_id"], "event": "COMMENT", "body": "", "comments": inline})
        if res is not None:
            log.append(f"review {repo}#{n}: {len(inline)} inline — {res.get('html_url', '')}")
            inline = []
        else:
            log.append(f"review {repo}#{n}: batch rejected ({err[:160]}), retrying one by one")
            rejected = []
            for c in inline:
                res, err = gh_api("POST", base, {"commit_id": r["commit_id"], "event": "COMMENT", "body": "", "comments": [c]})
                if res is not None:
                    log.append(f"  ok {c['path']}:{c['line']} — {res.get('html_url', '')}")
                else:
                    rejected.append(c)
                    log.append(f"  rejected {c['path']}:{c['line']} ({err[:120]})")
            general += [f"`{c['path']}:{c['line']}` — {c['body']}" for c in rejected]
    if general:
        res, err = gh_api("POST", f"repos/{repo}/issues/{n}/comments", {"body": "\n\n".join(general)})
        log.append(f"comment {repo}#{n}: " + (res.get("html_url", "") if res is not None else f"FAILED {err[:200]}"))


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: post.py <plan-file>")
    path = sys.argv[1]
    if os.path.exists(path + ".sent"):
        sys.exit(f"pr-assistant: plan already sent ({path}.sent); build a new plan")
    plan = json.load(open(path))
    log = []

    for pr in plan.get("prs", []):
        followup(pr, log)

    for r in plan.get("reviews", []):
        post_review(r, log)

    with open(path + ".sent", "w") as f:
        f.write("\n".join(log) + "\n")
    print("\n".join(log))


if __name__ == "__main__":
    main()
