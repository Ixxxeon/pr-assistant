---
name: review-requests
description: Review every open GitHub PR where a review is requested from the user personally, using Claude Code /code-review, report findings briefly per repository, and post selected findings as PR review comments only after typed approval.
disable-model-invocation: true
argument-hint: "[low|medium|high] [jobs=N] [repo, task key or PR URL filter]"
---

# Review requested PRs

Scripts: `S="${CLAUDE_PLUGIN_ROOT}/scripts"`. State (reviews, plans): `${PR_ASSISTANT_STATE_DIR:-~/.cache/pr-assistant}`.
Talk to the user in the language they write in. Examples below are in Russian; when the user writes in another language, translate their labels too (e.g. `подтверждено` → `confirmed`, `вероятно` → `likely`, `без замечаний` → `no findings`, `между PR` → `cross-PR`, `можно мерджить` → `ready to merge`). Set `"lang"` in the plan to `"ru"` for Russian and `"en"` otherwise; it sets the language of the plan preview. Keep terminal output short.

## Hard rules

- **Never write to GitHub except through `python3 "$S/post.py" <plan>` run alone, after the user typed `ok <CODE>` for that plan.** No `gh pr comment`, `gh pr review`, `gh api -X POST`, no approving, no requesting changes. A hook enforces this; if it denies, do not look for another way — ask the user.
- Never modify the user's own clones or branches. Reviews run in worktrees under `<clone folder>/.pr-worktrees/`.
- Only review the diff of the PR. Findings about untouched code are out of scope unless the PR makes them worse.

## 0. Clone folder

```bash
python3 "$S/config.py" get repos_dir
```

Exit code 4 means the folder for repository clones is not set yet (first run). Ask the user once where their repositories live or should be cloned (AskUserQuestion; offer `~/projects`, `~/src`, `~/.cache/pr-assistant/repos` for separate clones, and "Other"), then save it:

```bash
python3 "$S/config.py" set repos_dir <path>
```

Existing clones there are reused when their `origin` is the same GitHub repo (`<folder>/<repo>` or `<folder>/<owner>/<repo>`); missing ones are cloned into it. If any script later exits with code 4, do the same and rerun it.

## 1. Collect

Arguments: `$ARGUMENTS` — optional effort (`low|medium|high`, default `medium`), optional parallelism `jobs=N` (default 2, at most 4) and an optional filter (repository name, task key like `PROJ-1234`, or PR URL).

```bash
"$S/list-review-requests.sh"
```

This lists open PRs where review is requested from the user personally; team-only requests are excluded by design. Each PR has `task`: PRs of one task (shared ticket key in title or branch, same branch name across repos, or a link between PR bodies) get the same `task`, standalone PRs get `null`. Apply the filter; a task-key filter keeps the whole group. Skip drafts unless the filter names them. If nothing is left, say so with one line and stop.

Before starting, print one line: how many PRs, which task groups (`PROJ-1234: 5 PR`), how many run at once.

## 2. Review in parallel

Save the filtered list (the JSON array as printed, with `task`) to `${PR_ASSISTANT_STATE_DIR:-~/.cache/pr-assistant}/review-input.json` and run:

```bash
python3 "$S/review-batch.py" <effort> <jobs> < <filtered.json>
```

Run it with the Bash tool's `run_in_background`: a batch takes longer than the tool timeout. You are notified when it exits; do not poll. If the user asks about progress meanwhile, read `<run_dir>/progress.log` (path is in the first stderr line).

The batch runs up to `jobs` reviews at once, each `review-pr.sh` in its own process (worktree, `/code-review`, cache by head SHA). When every PR of a task group is reviewed, it runs one extra read-only pass over the whole group (`cross-review.sh`) that looks only for problems between the PRs: contracts between services, migrations vs code, library versions, deploy order.

At the end it prints JSON: `reviews` (per PR: `status`, `file` with `worktree`, `merge_base`, `head_sha`, `url`, `review_text`), `cross` (per group: `status`, `file` whose `result` holds the findings), `limit_hit`.

- `status: limit` / `limit_hit: true` — the Claude usage limit was hit; nothing new was started. Report what was reviewed and list the rest.
- `status: not_configured` — step 0 was skipped: ask for the clone folder, save it, rerun the batch.
- `status: error` — show the `error` line for that PR and go on with the others.
- Do not run `review-pr.sh` or `cross-review.sh` yourself in parallel Bash calls; the batch handles locking and ordering.

## 3. Report

Split each review into atomic findings. For each keep: `file:line`, the essence in one line (what breaks and when), severity (🔴 bug that breaks behaviour/data/security, 🟡 minor), and confidence (`подтверждено` if the review verified it in code, `вероятно` otherwise). Drop pure style remarks unless the review marked them as bugs. Number findings **globally** across all PRs so the user can pick them by number.

When a single-PR review says it could not check something because the other side lives in another repo, and the cross-review of its group answers it, keep the cross-review answer and drop the open question.

One block per task group, then one block per repository for standalone PRs. Inside a group, cross-PR findings go first, under `между PR`; each is anchored to the PR where the fix belongs and names the other side:

```
## PROJ-1234 — 5 PR
https://github.com/acme/orders-service/pull/65 и ещё 4
  между PR:
  1. 🔴 orders-service#65 OrderGroupEventProducer.java:88 — шлёт `groupId`, billing-service#69 читает `orderGroupId`: инвойс не создаётся (подтверждено)
#65 orders-service: Order groups: service, events… — alice-dev
https://github.com/acme/orders-service/pull/65
  2. 🟡 …
#14 catalog-service: … — без замечаний

## mail-service
#8 Guest access to mailboxes — bob-k
https://github.com/acme/mail-service/pull/8
  3. 🔴 MailboxServiceImpl.java:412 — web create отдаёт письма чужого ящика по mailbox_id из запроса (подтверждено)
#9 … — без замечаний
```

Then ask one question: which items to post, e.g. `1,3`, `все по PROJ-1234`, `ничего`, or a correction like `2: <свой текст>`.

## 4. Draft comments for the chosen items

Write each comment for a colleague, in the language of the PR discussion (the user's language if unclear):

- One or two sentences: what is wrong and what to do. No preamble, no praise, no summary of the PR, no mention of tools or AI.
- Phrase by confidence:
  - confirmed → direct: «Тут лучше …», «Здесь … — нужно …» / "Better use … here", "This … needs …".
  - likely → soft: «Может, лучше …?», «Похоже, …, стоит проверить» / "Maybe … instead?", "Looks like …, worth checking".
- Name the concrete thing to use or check (method, class, condition) instead of generic advice.
- If the `writing-plainly` skill is available, apply it to every draft before building the plan.
- Anchor inline comments to the new-file line (RIGHT side). Confirm the line is inside the PR diff (`git -C <worktree> diff <merge_base>...<head_sha> -- <path>`); otherwise put the item into `general` with `file:line` in the text.
- A cross-PR finding is posted on the PR where the fix belongs and names the other PR by link, so the author can find the other side.
- A user-provided text (`2: …`) is used as is, only anchored.

## 5. Plan, approve, post

Build one plan for all chosen items (one review per PR, `commit_id` = the reviewed head SHA):

```bash
python3 "$S/plan.py" <<'JSON'
{"kind":"review","lang":"ru","reviews":[{"repo":"Owner/name","number":8,"commit_id":"<head_sha>",
  "comments":[{"n":1,"path":"src/…/MailboxServiceImpl.java","line":412,"body":"…"}],
  "general":[]}]}
JSON
```

Show the printed preview to the user unchanged, including the last line `ok <CODE>`, and wait. When the user answers exactly `ok <CODE>`, run **only**:

```bash
python3 "$S/post.py" <PLAN_FILE>
```

and print the links it returns. If the user changes anything instead, rebuild the plan (it gets a new code) and ask again. Posting is always a `COMMENT` review: the plugin never approves or requests changes.
