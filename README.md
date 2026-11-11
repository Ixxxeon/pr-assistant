# pr-assistant

**English** | [Русский](README.ru.md)

A plugin for Claude Code and Codex with two workflows for GitHub pull requests. By default, reviews use the app you launched the plugin from: Claude Code runs Claude, Codex runs Codex. No reviewer selection is needed on installation or first use.

- `/pr-assistant:review-requests` finds open PRs where a review is requested from you personally (team requests don't count) and runs Claude Code `/code-review` on each, several PRs at a time. PRs that belong to one task get an extra check against each other. You get a short report grouped by task and repository, with numbered findings. Next to each PR link there is a mark: `👀` means the PR is worth reading yourself (large, or touches migrations, security, money, contracts between services), `⚡` means the findings are enough. Pick the numbers, and it drafts short comments and posts them as one review per PR after your approval. PRs with no findings are offered for approval; pick the ones to approve in the same answer.
- `/pr-assistant:my-prs` goes through your open PRs, finds unresolved comments you haven't answered, makes the fixes you agree with (in a separate worktree, one commit per comment) and shows a summary. PRs with two approvals are marked ready to merge. After your approval it does three things per PR: pushes the fixes, sends all replies as one review (like "Submit review" in the UI, so the reviewer gets one notification instead of a pile) and re-requests review from those waiting for it ("Re-request review"). Replies are short: "Done", "Added the javadoc". If the push fails, nothing is sent for that PR, so "Done" never goes out without the fix.

The command names above are for Claude Code. In Codex select the installed skill with `$` or `/skills` (for example, `$review-requests`). Both reports link to the PRs. The plugin answers in your language and writes comments in the language of the PR discussion. In Codex, the first workflow uses native Codex review instead of Claude `/code-review`; the follow-up workflow runs in the current host without a separate reviewer.

## Nothing reaches GitHub without your approval

Every write to GitHub (comments, replies, `git push`) goes into a plan. The plan gets a 6-character code derived from its content. Only `post.py` sends it. The host's hook requires your latest message to be exactly `ok <CODE>` and issues a one-use receipt for the session and plan. `post.py` checks that receipt before any write:

```
To send exactly this, reply: ok 039CD8
> ok 039CD8
```

- "yes" or "go ahead" is not accepted; it has to be the code. If the plan changes after approval, the code changes too and sending is blocked.
- While a pr-assistant session is running, direct `gh pr comment/review/merge`, `gh api` with POST/PATCH/PUT/DELETE, GraphQL mutations and `git push` are denied.
- The hook answers `deny`, which also applies in `bypassPermissions` mode.
- Commits, replies and comments carry no trace of the assistant: no `Co-Authored-By`, no "Generated with Claude", no mention of Claude or AI. The hook denies a commit with such a line.
- Other sessions are not affected. If hooks are missing or not trusted, the startup check fails and `post.py` refuses to send.
- Comments are posted as a `COMMENT` review; the plugin never requests changes. It approves only a PR with no findings, only if you chose it in the plan, and only if the PR has no new commits since the review. It doesn't resolve threads either; that's the reviewer's call.

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

The default setting is `reviewer=auto`. Host-specific hooks supply the host and session id; the skills pass them to the scripts on every invocation. Auto is resolved each time, so the same installation can be used from both apps. It never picks whichever executable happens to appear first on PATH. A missing CLI or usage limit does not trigger a switch to another provider.

- Claude runs `claude -p "/code-review <effort> <base>...<head>"` in the worktree, with a read-only tool allowlist and no MCP servers.
- Codex runs `codex exec review --base <base>` at the reviewed HEAD, with a read-only sandbox, no approval escalation, web search disabled, and user config/rules, plugins, apps and MCP configuration excluded from the worker. The worker uses the Codex CLI default model unless `codex_model` is set; it does not inherit the interactive chat's model.
- Both remove GitHub token environment variables and use an empty `gh` config directory. The orchestrator still uses your normal GitHub authentication to collect PRs and prepare worktrees.

Adapters return the same result format. Failed, empty or unfinished responses are errors, never clean reviews. Cache keys include provider, model setting, effort, CLI version, adapter/prompt version, full repository identity and both commit boundaries. Old Claude-only caches are not reused.

Pass `low`, `medium` (default), or `high` in the skill invocation. By default 2 reviews run at once; use `jobs=N`, at most 4. Reviews use the selected CLI's existing authentication and billing. If a usage limit is reached, new reviews stop and the report lists the unfinished work.

To override auto, ask the assistant to change the reviewer, or run from this checkout:

```bash
python3 scripts/config.py set reviewer codex  # or claude
python3 scripts/config.py set reviewer auto  # follow the current host again
python3 scripts/config.py set codex_model <model-id>
python3 scripts/config.py set claude_model <model-id>
```

`PR_ASSISTANT_REVIEWER` overrides the saved setting. `PR_ASSISTANT_CODEX_MODEL` and `PR_ASSISTANT_CLAUDE_MODEL` override model settings. For direct terminal runs, set the reviewer explicitly or set `PR_ASSISTANT_HOST=claude|codex`. Normal skill use needs neither manual setup nor a provider question.

## Re-reviews

Before each review the plugin looks at what has already been said on the PR and picks a mode:

- you haven't commented on this PR yet — a normal review;
- you reviewed it before and new commits came in — only the changes after your last review are reviewed, and only confirmed 🔴 blockers make it into the report. If `main` was merged into the branch or the history was rewritten since, the whole PR is reviewed with the same filter;
- no new commits since your review, or the author hasn't replied to a thread you started — no review runs; the PR is listed as skipped with the reason. A thread the author replied to counts as resolved, even if nobody resolved it.

A finding already raised in any comment on the PR (yours or someone else's, resolved or not) is dropped from the report. This keeps the plugin from repeating what was said and from endless review rounds with new nitpicks. You can force a skipped PR: "recheck #65".

## Related PRs

One task often touches several repositories. PRs count as one task if they share a ticket key in the title or branch (`PROJ-1234`), have the same branch name (except `main`, `develop`, `release/*` and the like), or one PR links to another in its description. The report shows them as one block.

Once the PRs in a group have been reviewed, one more read-only pass runs over the group (`cross-review.sh`). It looks for mismatched producer/consumer contracts, migrations vs entities and DAOs, config names, shared library versions and deploy order. Both adapters receive diff files and paths to the full worktrees. Claude uses read tools without shell; Codex reads through its read-only sandbox. The group cache includes the reviewer settings and each PR's base and head SHA.

## Installation

Requires `gh` (with `gh auth login`), `git`, Python 3.9+ and a signed-in CLI for the selected reviewer. Codex support targets CLI 0.167.0 or later. You only need both CLIs if you want to use both reviewers.

In Claude Code:

```
/plugin marketplace add Ixxxeon/pr-assistant
/plugin install pr-assistant@pr-assistant
```

For development, add the plugin from a local folder: `/plugin marketplace add /path/to/pr-assistant`.

For Codex, register this checkout and install its plugin:

```bash
codex plugin marketplace add /path/to/pr-assistant
codex plugin add pr-assistant@pr-assistant
```

After this version is published, the marketplace source can also be `Ixxxeon/pr-assistant`. Restart the session. Review and trust the plugin hooks in `/hooks`, or in the desktop app's Settings > Hooks. Codex requires this trust step; installation alone does not activate hooks. The first skill invocation checks both hooks and the selected CLI, then reuses the saved clone folder or asks for it once. It does not ask which reviewer to use.

Install the whole plugin, not just the skill folders: scripts and approval hooks are required. Hook trust must be reviewed again when Codex marks changed definitions as untrusted. See [Codex hooks](https://learn.chatgpt.com/docs/hooks) and [plugin packaging](https://developers.openai.com/plugins/build/plugins).

If you have the `writing-plainly` skill installed, comments go through it, which makes them shorter and plainer. The plugin works without it.

## Layout

```
.claude-plugin/        Claude plugin and marketplace manifests
.codex-plugin/         Codex plugin manifest
.agents/plugins/       Codex marketplace
hooks/                 host-specific session/prompt/tool hooks → scripts/guard.py
skills/review-requests SKILL.md of the first command
skills/my-prs          SKILL.md of the second command
scripts/
  list-review-requests.sh  PRs where review is requested from you personally
  list-my-prs.sh           your open PRs
  pr-status.py             approvals, merge readiness, unanswered threads
  prepare-worktree.sh      clone + worktree (review: detached, own: branch pr-assistant/<N>)
  group-prs.py             groups PRs by task
  review-batch.py          parallel run of reviews and group checks
  review-history.py        what was already said on the PR, review mode
  review-pr.sh             worktree + review + cache for one PR
  run-code-review.sh       selected read-only reviewer
  cross-review.sh          read-only check of a PR group against each other
  reviewer.py              auto selection, worker execution, cache fingerprint
  reviewers/               Claude and Codex adapters, shared cross-PR prompt
  doctor.py                checks host, CLI and approval hooks
  approval.py              one-use session/plan approval receipts
  config.py                clone folder: read, write, find a clone
  locked.py                locks a clone during fetch/worktree
  plan.py                  plan of GitHub writes and its approval code
  post.py                  sends an approved plan
  guard.py                 safety hook
```

## Tests

Offline tests: `python3 -m unittest discover -s tests -v`. They use temporary repositories and fake CLIs; they do not contact GitHub or model providers.

## License

MIT, see [LICENSE](LICENSE).
