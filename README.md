# pr-assistant

**English** | [Русский](README.ru.md)

A Claude Code plugin with two commands for GitHub pull requests:

- `/pr-assistant:review-requests` finds open PRs where a review is requested from you personally (team requests don't count) and runs Claude Code `/code-review` on each, several PRs at a time. PRs that belong to one task get an extra check against each other. You get a short report grouped by task and repository, with numbered findings. Pick the numbers, and it drafts short comments and posts them as one review per PR after your approval.
- `/pr-assistant:my-prs` goes through your open PRs, finds unresolved comments you haven't answered, makes the fixes you agree with (in a separate worktree, one commit per comment) and shows a summary. PRs with two approvals are marked ready to merge. After your approval it does three things per PR: pushes the fixes, sends all replies as one review (like "Submit review" in the UI, so the reviewer gets one notification instead of a pile) and re-requests review from those waiting for it ("Re-request review"). Replies are short: "Done", "Added the javadoc". If the push fails, nothing is sent for that PR, so "Done" never goes out without the fix.

Both reports link to the PRs. The plugin answers in the language you write to Claude in, and writes PR comments in the language of the PR discussion.

## Nothing reaches GitHub without your approval

Every write to GitHub (comments, replies, `git push`) goes into a plan. The plan gets a 6-character code derived from its content. Only `post.py` sends it, and a Claude Code hook lets it run only if your latest message contains `ok <CODE>`:

```
To send exactly this, reply: ok 039CD8
> ok 039CD8
```

- "yes" or "go ahead" is not accepted; it has to be the code. If the plan changes after approval, the code changes too and sending is blocked.
- While a pr-assistant session is running, direct `gh pr comment/review/merge`, `gh api` with POST/PATCH/PUT/DELETE, GraphQL mutations and `git push` are denied.
- The hook answers `deny`, which also applies in `bypassPermissions` mode.
- Other Claude Code sessions are not affected.
- Reviews are always posted as `COMMENT`: the plugin never approves or requests changes. It doesn't resolve threads either; that's the reviewer's call.

Limitation: the hook checks command text. It protects against accidental sends, not against a model deliberately looking for a way around it. For example, it won't stop a script of its own that calls `gh`. The skills explicitly forbid doing that.

## Where files live

| What | Where |
|---|---|
| Repository clones | a folder the plugin asks for on first run |
| PR worktrees | `<clone folder>/.pr-worktrees/<repo>-<N>` |
| Review results, plans, hook state | `~/.cache/pr-assistant/` (`PR_ASSISTANT_STATE_DIR`) |

On first run the plugin asks where your repositories live and saves the answer to `~/.config/pr-assistant/config.json`. If a repository is already cloned there (`<folder>/<repo>` or `<folder>/<owner>/<repo>`, with `origin` pointing to the same GitHub repository), the plugin uses that clone. Otherwise it clones it there. You can change the folder with `config.py set repos_dir <path>` or with `PR_ASSISTANT_REPOS_DIR` in `~/.claude/settings.json`:

```json
{ "env": { "PR_ASSISTANT_REPOS_DIR": "/path/to/repos" } }
```

The plugin doesn't touch your working copy: it doesn't switch branches or touch uncommitted changes. In a clone it only runs `fetch` and adds worktrees. Reviews run in a separate worktree at the PR head. Fixes to your own PRs are made in a worktree on branch `pr-assistant/<N>` and pushed to the PR branch fast-forward only, never with `--force`.

## How the review works

`/code-review` runs as a separate process, `claude -p "/code-review medium <merge-base>...<head>"`, inside the worktree. That process:

- cannot edit files, use `gh`, `git push`, the web or MCP servers;
- has no GitHub token in its environment;
- can only read code and run git.

Results are cached by head SHA: if a PR hasn't changed since the last run, a rerun costs nothing. Pass the effort level as an argument: `/pr-assistant:review-requests high`. The default is `medium`; in my runs it produced little noise.

Reviews use your Claude subscription limits. A small PR takes 1–2 minutes and 150–700K tokens, a large one up to 3.6M. By default 2 reviews run at once; set it with `jobs=N` (at most 4): `/pr-assistant:review-requests jobs=3`. Parallelism doesn't save tokens per PR; it spends the same limit faster. If you hit the limit, no new reviews start, the running ones finish, and the run shows what's left.

## Related PRs

One task often touches several repositories. PRs count as one task if they share a ticket key in the title or branch (`PROJ-1234`), have the same branch name (except `main`, `develop`, `release/*` and the like), or one PR links to another in its description. The report shows them as one block.

Once every PR in a group has been reviewed on its own, one more read-only pass runs over the whole group (`cross-review.sh`). It looks only for what a single PR can't show: event and DTO fields that don't match between producer and consumer, migrations vs entities and DAOs, config property names, shared library versions, deploy order. This pass gets the diffs as files and read access to every worktree in the group; it has no shell, no edits, no `gh`, no web and no MCP. Its result is cached by the group's set of head SHAs.

## Installation

Requires `gh` (with `gh auth login`), `git`, `python3` and Claude Code.

```
/plugin marketplace add Ixxxeon/pr-assistant
/plugin install pr-assistant@pr-assistant
```

For development, add the plugin from a local folder: `/plugin marketplace add /path/to/pr-assistant`.

If you have the `writing-plainly` skill installed, comments go through it, which makes them shorter and plainer. The plugin works without it.

## Layout

```
.claude-plugin/        plugin and marketplace manifests
hooks/hooks.json       UserPromptSubmit and PreToolUse(Bash) hooks → scripts/guard.py
skills/review-requests SKILL.md of the first command
skills/my-prs          SKILL.md of the second command
scripts/
  list-review-requests.sh  PRs where review is requested from you personally
  list-my-prs.sh           your open PRs
  pr-status.py             approvals, merge readiness, unanswered threads
  prepare-worktree.sh      clone + worktree (review: detached, own: branch pr-assistant/<N>)
  group-prs.py             groups PRs by task
  review-batch.py          parallel run of reviews and group checks
  review-pr.sh             worktree + review + cache for one PR
  run-code-review.sh       read-only /code-review run
  cross-review.sh          read-only check of a PR group against each other
  check-claude-output.py   parses claude -p output, detects the usage limit
  config.py                clone folder: read, write, find a clone
  locked.py                locks a clone during fetch/worktree
  plan.py                  plan of GitHub writes and its approval code
  post.py                  sends an approved plan
  guard.py                 safety hook
```

## License

MIT, see [LICENSE](LICENSE).
