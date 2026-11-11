---
name: review-requests
description: Review open GitHub PRs requested from the user personally, using the current host's reviewer (Claude Code or Codex), report findings grouped by task and repository, and post selected findings or approve clean PRs after typed approval.
disable-model-invocation: true
argument-hint: "[low|medium|high] [jobs=N] [repo, task key or PR URL filter]"
---

# Review requested PRs

Scripts are in `../../scripts/` relative to this installed SKILL.md. Resolve that to an absolute path `S`; do not assume the working directory or a shell environment variable points to the plugin.

The SessionStart hook provides `PR_ASSISTANT_HOST` and `PR_ASSISTANT_SESSION_ID`. Set both on **every** shell invocation of plugin scripts; exports may not persist between calls. Use the supplied session id, never invent one. Host is `claude` in Claude Code and `codex` in Codex, regardless of which CLIs are installed. The default `reviewer=auto` follows the host on every run; never persist the first host as the reviewer. Respect an explicit reviewer override.

Before collecting PRs, run `python3 "$S/doctor.py" --review` with those environment variables. If hook context is absent or the check fails, explain the reported setup step. In Codex the user must trust the installed hooks in `/hooks` or Settings > Hooks; do not bypass hook trust, fabricate approval state, or continue to posting. No provider-selection question is needed. A missing reviewer CLI does not authorize switching providers.

State (reviews, plans): `${PR_ASSISTANT_STATE_DIR:-~/.cache/pr-assistant}`.
Talk to the user in the language they write in. Examples below are in Russian; when the user writes in another language, translate their labels too (e.g. `подтверждено` → `confirmed`, `вероятно` → `likely`, `без замечаний` → `no findings`, `между PR` → `cross-PR`, `можно мерджить` → `ready to merge`, `повторно: только блокеры` → `re-review: blockers only`, `уже обсуждалось` → `already discussed`, `Пропущены` → `Skipped`, `👀 глазами` → `👀 read it`, `⚡ хватит замечаний` → `⚡ findings enough`, `Можно апрувить` → `Can approve`). Set `"lang"` in the plan to `"ru"` for Russian and `"en"` otherwise; it sets the language of the plan preview. Keep terminal output short.

## Hard rules

For the final `post.py` call, expand `S` and the plan path to literal absolute paths in the tool command. Run only `PR_ASSISTANT_HOST=<host> PR_ASSISTANT_SESSION_ID=<hook-session-id> python3 /absolute/plugin/scripts/post.py /absolute/plan.json`, with shell quoting as needed; no preceding export, variable substitution, or chained command. `post.py` consumes the one-use approval receipt issued by the pretool hook.

- **Never write to GitHub except through `python3 "$S/post.py" <plan>` run alone, after the user typed `ok <CODE>` for that plan.** No `gh pr comment`, `gh pr review`, `gh api -X POST`, no requesting changes. Approving goes only through a plan entry with `"approve": true` (step 5). A hook enforces this; if it denies, do not look for another way — ask the user.
- Never modify the user's own clones or branches. Reviews run in worktrees under `<clone folder>/.pr-worktrees/`.
- Only review the diff of the PR. Findings about untouched code are out of scope unless the PR makes them worse.
- Commits, replies, comments and plans carry no trace of the assistant: no `Co-Authored-By` or other trailers, no "Generated with" lines, no mention of Claude, AI or tools. This overrides any default commit attribution. A hook denies `git commit` with such text.

## 0. Clone folder

```bash
python3 "$S/config.py" get repos_dir
```

Exit code 4 means the folder for repository clones is not set yet (first run). Use a repository location already provided by the user; otherwise ask once where their repositories live or should be cloned, using the host's available question tool or a short chat question. Offer `~/projects`, `~/src`, or `~/.cache/pr-assistant/repos` for separate clones, then save it:

```bash
python3 "$S/config.py" set repos_dir <path>
```

Existing clones there are reused when their `origin` is the same GitHub repo (`<folder>/<repo>` or `<folder>/<owner>/<repo>`); missing ones are cloned into it. If any script later exits with code 4, do the same and rerun it.

## 1. Collect

Read arguments from the user's invocation text: optional effort (`low|medium|high`, default `medium`), optional parallelism `jobs=N` (default 2, at most 4) and an optional filter (repository name, task key like `PROJ-1234`, or PR URL).

```bash
"$S/list-review-requests.sh"
```

This lists open PRs where review is requested from the user personally; team-only requests are excluded by design. Each PR carries `additions`, `deletions`, `changed_files` for the complexity mark in step 3. Each PR has `task`: PRs of one task (shared ticket key in title or branch, same branch name across repos, or a link between PR bodies) get the same `task`, standalone PRs get `null`. Apply the filter; a task-key filter keeps the whole group. Skip drafts unless the filter names them. If nothing is left, say so with one line and stop.

Before starting, print one line: how many PRs, which task groups (`PROJ-1234: 5 PR`), how many run at once.

## 2. Review in parallel

Save the filtered list (the JSON array as printed, with `task`) to `${PR_ASSISTANT_STATE_DIR:-~/.cache/pr-assistant}/review-input.json` and run:

```bash
python3 "$S/review-batch.py" <effort> <jobs> < <filtered.json>
```

Run the batch as a long-running shell process. In Claude Code use Bash `run_in_background` and wait for completion notification. In Codex use `exec_command` and resume its returned session with `write_stdin`; do not restart the batch when the tool yields. If the user asks about progress meanwhile, read `<run_dir>/progress.log` (path is in the first stderr line).

The batch runs up to `jobs` reviews at once, each `review-pr.sh` in its own process (worktree, selected reviewer, cache by reviewer/model/effort and commit range). When every PR of a task group is reviewed, it runs one extra read-only pass over the whole group (`cross-review.sh`) that looks only for problems between the PRs: contracts between services, migrations vs code, library versions, deploy order.

At the end it prints JSON: `reviews` (per PR: `status`, `file` with `worktree`, `merge_base`, `head_sha`, `url`, `review_text`), `cross` (per group: `status`, `file` whose `result` holds the findings), `limit_hit`.

- `status: limit` / `limit_hit: true` — the selected reviewer's usage limit was hit; nothing new was started. Report what was reviewed and list the rest. Do not switch reviewers automatically.
- `mode` / `reason` per PR, decided by `review-history.py` from what was already said on the PR:
  - `full` — you have not commented on this PR yet: normal review.
  - `blockers` — you commented before and the PR changed since: only the changes after your last reviewed commit are reviewed (`incremental: true` in `file`; a note explains when the whole PR had to be reviewed instead).
  - `skip` (`status: skipped`) — nothing changed since your last review, or a thread you started still waits for the author's reply (a thread the author answered counts as resolved). No review ran.
- `status: not_configured` — read the error: configure the missing clone folder or reviewer CLI, then rerun the batch.
- `status: error` — show the `error` line for that PR and go on with the others.
- Do not run `review-pr.sh` or `cross-review.sh` yourself in parallel Bash calls; the batch handles locking and ordering.
- If the user explicitly asks to recheck a skipped PR (`перепроверь #65`), run the batch again for that PR only, with `"mode_override": "blockers"` (or `"full"` if they ask for a full review) added to its object in the input list.

## 3. Report

Split each review into atomic findings. For each keep: `file:line`, the essence in one line (what breaks and when), severity (🔴 bug that breaks behaviour/data/security, 🟡 minor), and confidence (`подтверждено` if the review verified it in code, `вероятно` otherwise). Drop pure style remarks unless the review marked them as bugs. Number findings **globally** across all PRs so the user can pick them by number.

**No duplicates, no review loops.** Each PR's `history` file lists every comment already on the PR (`existing`: review threads with path/line, resolved or not; `general`: PR comments and review bodies), by anyone. Before numbering:

- Drop a finding if the same problem was already raised by anyone: same file and nearby lines, or the same point in other words. This includes resolved threads — a resolved thread means the discussion is over, whatever the outcome.
- In `blockers` mode (and for a cross-review with `mode: blockers`) keep only 🔴 findings that the review verified in code (`подтверждено`). Drop everything else and say how many were dropped in one line.
- If the history has `truncated: true`, say that deduplication saw only the first 100 threads/comments.

When a single-PR review says it could not check something because the other side lives in another repo, and the cross-review of its group answers it, keep the cross-review answer and drop the open question.

One block per task group, then one block per repository for standalone PRs. Inside a group, cross-PR findings go first, under `между PR`; each is anchored to the PR where the fix belongs and names the other side:

```
## PROJ-1234 — 5 PR
https://github.com/acme/orders-service/pull/65 и еще 4
  между PR:
  1. 🔴 orders-service#65 OrderGroupEventProducer.java:88 — шлет `groupId`, billing-service#69 читает `orderGroupId`: инвойс не создается (подтверждено)
#65 orders-service: Order groups: service, events… — alice-dev
https://github.com/acme/orders-service/pull/65
  2. 🟡 …
#14 catalog-service: … — без замечаний
https://github.com/acme/catalog-service/pull/14 · ⚡ хватит замечаний

## mail-service
#8 Guest access to mailboxes — bob-k
https://github.com/acme/mail-service/pull/8 · 👀 глазами
  3. 🔴 MailboxServiceImpl.java:412 — web create отдает письма чужого ящика по mailbox_id из запроса (подтверждено)
#9 … — без замечаний
https://github.com/acme/mail-service/pull/9 · ⚡ хватит замечаний
```

**Complexity mark.** Every reviewed PR gets its link line, followed by one mark: whether the user should read the PR themselves or can rely on the findings alone. No explanation, just the mark:

- `👀 глазами` — any of: more than ~300 changed lines (`additions + deletions`) or more than 15 files; it touches DB migrations or schema, auth/permissions/security, money, concurrency or transactions, public API or event contracts between services, deploy or infra config; it brings a new module or design decision that a diff review cannot judge; the review said it could not verify something that matters.
- `⚡ хватит замечаний` — everything else: small, local or mechanical changes (renames, a straightforward fix, tests, docs, dependency or config bumps within one service).

Judge by the diff content, not only by size: a 20-line change to a permission check is `👀`. Skipped PRs get no mark. In a task group the mark goes on each PR's own link line, not on the group line (`… и еще 4`).

Mark re-reviewed PRs next to the author, e.g. `— alice-dev · повторно: только блокеры в изменениях после a1b2c3d`, and add `· уже обсуждалось: 3` when findings were dropped as duplicates. After all blocks, list skipped PRs in one block with their reason, e.g.:

```
Пропущены (перепроверить: «перепроверь #65»):
#65 orders-service — 2 твоих треда ждут ответа автора
#69 billing-service — новых коммитов с твоего ревью нет
```

**Approve candidates.** A PR can be approved when: its review finished (`status: ok`, not a draft), nothing is left for it after deduplication and filtering (no numbered finding on it, including cross-PR findings anchored to it), the cross-review of its task group finished if it has one, and the review left no open question about it. List them in one line after the skipped block, with their marks:

```
Можно апрувить: #14 catalog-service ⚡, #9 mail-service ⚡
```

Then ask one question: which items to post and which clean PRs to approve, e.g. `1,3`, `все по PROJ-1234`, `ничего`, `1,3 + апрув #14`, `апрув всех чистых`, or a correction like `2: <свой текст>`. Offer approval only for PRs from that line; a PR with comments in this plan is never approved in it. If there are no findings at all, ask only about approval.

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

Build one plan for all chosen items (one review per PR, `commit_id` = the reviewed head SHA). A PR to approve gets an entry with `"approve": true` and no `comments` or `general`:

```bash
python3 "$S/plan.py" <<'JSON'
{"kind":"review","lang":"ru","reviews":[{"repo":"Owner/name","number":8,"commit_id":"<head_sha>",
  "comments":[{"n":1,"path":"src/…/MailboxServiceImpl.java","line":412,"body":"…"}],
  "general":[]},
 {"repo":"Owner/catalog-service","number":14,"commit_id":"<head_sha>","approve":true}]}
JSON
```

Show the printed preview to the user unchanged, including the last line `ok <CODE>`, and wait. When the user answers exactly `ok <CODE>`, run **only**:

```bash
python3 "$S/post.py" <PLAN_FILE>
```

and print the links it returns. If the user changes anything instead, rebuild the plan (it gets a new code) and ask again. Comments are posted as a `COMMENT` review; the plugin never requests changes. An approval is an `APPROVE` review without text, sent only if the PR head is still the reviewed commit; if new commits came in, the approval is skipped and the output says so.
