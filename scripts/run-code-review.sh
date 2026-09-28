#!/usr/bin/env bash
# run-code-review.sh <worktree> <merge_base> <head_sha> <out.json> [effort]
#
# Runs Claude Code's /code-review headless on merge_base...head_sha inside the
# worktree. The review session is read-only: no edits, no gh, no push, no MCP
# servers, and no GitHub token in its environment.
# Exit codes: 0 ok, 3 subscription/usage limit hit (stop the batch), 1 other error.
set -uo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
source "$S/common.sh"
WT=$1; MB=$2; HEAD=$3; OUT=$4; EFFORT=${5:-medium}
EMPTY_GH="$STATE_DIR/empty-gh-config"; mkdir -p "$EMPTY_GH"
cd "$WT" || die "no worktree $WT"
env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR="$EMPTY_GH" \
  claude -p "/code-review $EFFORT $MB...$HEAD" --model opus \
  --output-format json --setting-sources project --permission-mode default --no-session-persistence \
  --strict-mcp-config \
  --allowedTools Read Grep Glob Agent Task Skill "Bash(git diff:*)" "Bash(git log:*)" "Bash(git show:*)" \
    "Bash(git blame:*)" "Bash(git status:*)" "Bash(git rev-parse:*)" "Bash(git merge-base:*)" \
    "Bash(git ls-files:*)" "Bash(ls:*)" "Bash(rg:*)" "Bash(grep:*)" "Bash(find:*)" "Bash(cat:*)" \
    "Bash(wc:*)" "Bash(head:*)" "Bash(sed:*)" \
  --disallowedTools "Bash(gh:*)" "Bash(git push:*)" Edit Write NotebookEdit WebFetch WebSearch \
  > "$OUT" 2> "$OUT.err"
rc=$?
python3 "$S/check-claude-output.py" "$OUT"
py=$?
[[ $py -ne 0 ]] && exit $py
exit $rc
