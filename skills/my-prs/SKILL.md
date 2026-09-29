---
name: my-prs
description: Go through the user's own open GitHub PRs, find unresolved review comments that still need an answer, fix what is agreed in a separate worktree, highlight PRs with two approvals as ready to merge, and — after typed approval — push the fixes, post all replies as one review per PR and re-request review from the reviewers.
disable-model-invocation: true
argument-hint: "[repo or PR URL filter]"
---

# Follow up on my PRs

Scripts: `S="${CLAUDE_PLUGIN_ROOT}/scripts"`. State: `${PR_ASSISTANT_STATE_DIR:-~/.cache/pr-assistant}`.
Talk to the user in the language they write in. Examples below are in Russian; when the user writes in another language, translate their labels too (e.g. `подтверждено` → `confirmed`, `вероятно` → `likely`, `без замечаний` → `no findings`, `между PR` → `cross-PR`, `можно мерджить` → `ready to merge`). Set `"lang"` in the plan to `"ru"` for Russian and `"en"` otherwise; it sets the language of the plan preview. Keep terminal output short.

## Hard rules

- **Never write to GitHub except through `python3 "$S/post.py" <plan>` run alone, after the user typed `ok <CODE>` for that plan.** This covers `git push` too. A hook enforces it; if it denies, ask the user instead of looking for another way.
- Never touch the user's own clone, its branches or uncommitted work. Fixes are made in `<clone folder>/.pr-worktrees/<repo>-<number>` on the local branch `pr-assistant/<number>`. The one exception is `post.py`: after a push it fast-forwards the user's local `<head_ref>` to the pushed commit, and only when that is a pure fast-forward git accepts.
- Never resolve review threads: the reviewer decides that.
- Commits, replies, comments and plans carry no trace of the assistant: no `Co-Authored-By` or other trailers, no "Generated with" lines, no mention of Claude, AI or tools. This overrides any default commit attribution. A hook denies `git commit` with such text.

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

```bash
"$S/list-my-prs.sh"                       # open PRs authored by the user; apply $ARGUMENTS as a filter
"$S/pr-status.py" <owner/repo> <number>   # per PR: approvals, ready_to_merge, threads/comments needing reply,
                                          # reviewers, suggested_rerequest, pending_review, pr_node_id
```

A thread needs a reply when it is unresolved and its last comment is not the user's. Top-level comments by others after the user's last one are candidates too; skip plain approvals, LGTMs, CI/bot noise.

## 2. Work each comment that needs a reply

Prepare the worktree once per PR that has such comments:

```bash
"$S/prepare-worktree.sh" own <owner/repo> <number>   # JSON: worktree, head_ref, notes
```

Read `notes`: if the worktree keeps earlier local commits or changes, tell the user and build on them.

For each comment, read the code it points to and decide:

- **Agree, clear fix** → make the minimal change in the worktree. Run the repository's quick checks if AGENTS.md/README names them and they are cheap (compile, the affected tests); do not run long suites. Commit locally, **one commit per comment**, message `<JIRA-KEY from the PR title> review: <what changed>`. Draft a short reply: «Сделал», «Готово», «Добавил джавадок», «Переименовал в `X`», «Вынес в `Y`» / "Done", "Added the javadoc", "Renamed to `X`", "Moved to `Y`".
- **Agree, but the change is large or ambiguous** → do not change code; draft a one-line plan or question and mark it `нужно твоё решение`.
- **Disagree** → do not change code; draft a short reasoned reply (one or two sentences, concrete: why the current code is fine) and mark it `не согласен`.
- **Question** → draft a direct answer.

Replies are brief and plain, in the language of the thread. If the `writing-plainly` skill is available, apply it to every draft. No mention of tools or AI.

## 3. Report

One block per PR, with the link. Number items **globally**:

```
## profile-service
#61 PROJ-987 use common methods from libs — https://github.com/acme/profile-service/pull/61
  ✅ 2 аппрува (carol-r, dave-m) — можно мерджить
  1. carol-r · MailboxMapper.java:91 «переименовать deleteMailboxes» → поправил (a1b2c3d); ответ: «Переименовал в deleteGuestMailboxes»
  2. carol-r · MailboxServiceImpl.java:567 «не смешивать device и web» → не согласен; ответ: «…»
  → запушу 1 коммит, отвечу одним ревью, перезапрошу ревью: carol-r
#62 … — новых комментариев нет
```

- Mark every PR with `ready_to_merge: true` as `✅ можно мерджить`, even if nothing else is needed.
- For fixes, give the worktree path so the user can inspect them (`git -C <worktree> show <sha>`).
- If `pending_review` is true, warn: the user has an unsubmitted review on that PR; it must be submitted or discarded in the GitHub UI before replies can be sent.
- Re-request list per PR: reviewers from `suggested_rerequest` whose comments are answered in this batch, plus everyone in `changes_requested`. Never the user, never bots.

Ask one question: which items to send, e.g. `1,2`, `все`, `ничего`, a corrected reply `2: <текст>`, or a changed re-request list (`#61 перезапросить: carol-r`).

## 4. Plan, approve, send — one batch per PR

If some fixed items were not chosen, drop their commits before planning: rebuild the local branch from `origin/<head_ref>` with only the chosen commits (`git reset --hard origin/<head_ref>` + `git cherry-pick <shas>` inside the worktree — local only).

```bash
python3 "$S/plan.py" <<'JSON'
{"kind":"followup","lang":"ru",
 "prs":[{"repo":"Owner/name","number":61,"pr_node_id":"<pr_node_id>",
         "push":{"worktree":"<worktree>","remote_branch":"<head_ref>"},
         "replies":[{"n":1,"thread_id":"<thread_id>","body":"Сделал"},
                    {"n":2,"thread_id":"<thread_id>","body":"…"}],
         "general":[{"n":3,"body":"…ответ на общий комментарий…"}],
         "rerequest":["carol-r"]}]}
JSON
```

- `push` only when the PR has chosen fix commits; `thread_id` and `pr_node_id` come from `pr-status.py`.
- Answers to top-level comments go to `general` (they become the body of the same review, one short paragraph each, addressed by `@login` if needed).
- `post.py` does, per PR: push (fast-forward only) → one COMMENT review holding all replies → re-request review. If the push fails, nothing else is sent for that PR, so «Сделал» / "Done" never goes out without the fix.

Show the preview unchanged with the `ok <CODE>` line and wait. On exactly `ok <CODE>`, run only `python3 "$S/post.py" <PLAN_FILE>` and print the result links. Any change → new plan, new code.
