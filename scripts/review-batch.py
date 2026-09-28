#!/usr/bin/env python3
"""review-batch.py [effort] [jobs] < prs.json

Reviews a list of PRs (the output of list-review-requests.sh, already filtered)
with up to `jobs` reviews running at once (default 2, at most 4). Each review is
review-pr.sh in its own process. When every PR of a task group (same `task`) is
reviewed, one cross-review.sh pass checks the PRs against each other.

If a review hits the Claude usage limit (exit 3) or the clone folder is not
configured (exit 4), nothing new is started; the
running reviews finish and the rest is reported as not started.

Progress goes to <run_dir>/progress.log and stderr. At the end prints JSON:
  {run_dir, reviews: [{repo, number, task, status, file, seconds, error}],
   cross: [{task, prs, status, file, seconds, error}], limit_hit}
status: ok | error | limit | not_configured | not_started. `file` is review-pr.sh's JSON
(worktree, merge_base, head_sha, url, review_text) or, for cross, the claude
output whose `result` holds the findings.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

S = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.expanduser(os.environ.get("PR_ASSISTANT_STATE_DIR", "~/.cache/pr-assistant"))
MAX_JOBS = 4

effort = sys.argv[1] if len(sys.argv) > 1 else "medium"
jobs = max(1, min(MAX_JOBS, int(sys.argv[2]) if len(sys.argv) > 2 else 2))
prs = json.load(sys.stdin)
run_dir = os.path.join(STATE_DIR, "batches", time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")
os.makedirs(run_dir)
log_lock = threading.Lock()


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    with log_lock:
        print(line, file=sys.stderr, flush=True)
        with open(os.path.join(run_dir, "progress.log"), "a") as f:
            f.write(line + "\n")


def short(pr):
    return f"{pr['repo'].split('/')[-1]}#{pr['number']}"


def status_of(rc):
    return {0: "ok", 3: "limit", 4: "not_configured"}.get(rc, "error")


def tail(path, n=5):
    try:
        with open(path) as f:
            return "".join(f.readlines()[-n:]).strip()
    except OSError:
        return ""


def review(pr):
    name = f"{pr['repo'].split('/')[-1]}-{pr['number']}"
    out, err = os.path.join(run_dir, name + ".json"), os.path.join(run_dir, name + ".log")
    t0 = time.time()
    with open(out, "w") as o, open(err, "w") as e:
        rc = subprocess.call([os.path.join(S, "review-pr.sh"), pr["repo"], str(pr["number"]), effort],
                             stdout=o, stderr=e)
    res = {"repo": pr["repo"], "number": pr["number"], "task": pr.get("task"), "status": status_of(rc),
           "file": out, "seconds": round(time.time() - t0)}
    if rc != 0:
        res["error"] = tail(err)
    return res


def cross(task, members):
    infos = [dict(json.load(open(r["file"])), repo=r["repo"], number=r["number"],
                  title=next(p.get("title", "") for p in prs if p["repo"] == r["repo"] and p["number"] == r["number"]))
             for r in members]
    infos.sort(key=lambda i: (i["repo"], i["number"]))
    key = hashlib.sha256("|".join(f"{i['repo']}#{i['number']}@{i['head_sha']}" for i in infos).encode()).hexdigest()[:10]
    safe_task = "".join(c if c.isalnum() or c in "-_." else "_" for c in task)
    out = os.path.join(STATE_DIR, "reviews", f"cross-{safe_task}-{key}.json")
    res = {"task": task, "prs": [f"{i['repo']}#{i['number']}" for i in infos], "file": out}
    t0 = time.time()
    if os.path.exists(out) and os.path.getsize(out):
        log(f"{task}: cross-review cached")
        return dict(res, status="ok", seconds=0)
    spec = os.path.join(run_dir, f"cross-{safe_task}.spec.json")
    with open(spec, "w") as f:
        json.dump({"task": task, "prs": [{k: i[k] for k in ("repo", "number", "title", "url", "worktree",
                                                            "merge_base", "head_sha")} for i in infos]}, f)
    with open(os.path.join(run_dir, f"cross-{safe_task}.log"), "w") as e:
        rc = subprocess.call([os.path.join(S, "cross-review.sh"), spec, out], stdout=e, stderr=e)
    if rc != 0 and os.path.exists(out):
        os.replace(out, out + ".failed")
    res.update(status=status_of(rc), seconds=round(time.time() - t0))
    if rc != 0:
        res["error"] = tail(os.path.join(run_dir, f"cross-{safe_task}.log"))
    return res


groups = {}
for pr in prs:
    if pr.get("task"):
        groups.setdefault(pr["task"], []).append(pr)
groups = {t: m for t, m in groups.items() if len(m) > 1}
# Group members first, so a group's cross-review can start as early as possible.
queue = sorted(prs, key=lambda p: (p.get("task") not in groups, p.get("task") or "", p["repo"], p["number"]))
log(f"{len(prs)} PR, {len(groups)} task group(s), {jobs} at a time, effort {effort}; log: {run_dir}")

reviews, crosses, limit_hit = [], [], False
done_by_task = {t: [] for t in groups}
with ThreadPoolExecutor(max_workers=jobs) as pool:
    running = {}
    while queue or running:
        while queue and len(running) < jobs and not limit_hit:
            item = queue.pop(0)
            if isinstance(item, tuple):
                log(f"{item[0]}: cross-review of {len(item[1])} PR started")
                running[pool.submit(cross, *item)] = item
            else:
                log(f"{short(item)}: started")
                running[pool.submit(review, item)] = item
        if limit_hit and not running:
            break
        finished, _ = wait(running, return_when=FIRST_COMPLETED)
        for fut in finished:
            item = running.pop(fut)
            res = fut.result()
            if res["status"] in ("limit", "not_configured"):
                limit_hit = True
            if isinstance(item, tuple):
                crosses.append(res)
                log(f"{res['task']}: cross-review {res['status']} ({res['seconds']}s)")
                continue
            reviews.append(res)
            log(f"{short(item)}: {res['status']} ({res['seconds']}s)")
            task = res["task"]
            if task in groups:
                done_by_task[task].append(res)
                if len(done_by_task[task]) == len(groups[task]):
                    ok = [r for r in done_by_task[task] if r["status"] == "ok"]
                    if len(ok) > 1:
                        queue.insert(0, (task, ok))
                    else:
                        log(f"{task}: fewer than two PRs reviewed, no cross-review")

for item in queue:
    if isinstance(item, tuple):
        crosses.append({"task": item[0], "prs": [f"{r['repo']}#{r['number']}" for r in item[1]],
                        "status": "not_started"})
    else:
        reviews.append({"repo": item["repo"], "number": item["number"], "task": item.get("task"),
                        "status": "not_started"})
for task, members in groups.items():
    if task not in {c["task"] for c in crosses} and len(done_by_task[task]) < len(members):
        crosses.append({"task": task, "prs": [f"{p['repo']}#{p['number']}" for p in members],
                        "status": "not_started"})
if limit_hit:
    log("stopped: usage limit or clone folder not configured; nothing new was started")
print(json.dumps({"run_dir": run_dir, "reviews": reviews, "cross": crosses, "limit_hit": limit_hit},
                 ensure_ascii=False, indent=1))
