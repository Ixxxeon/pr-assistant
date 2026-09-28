#!/usr/bin/env bash
# cross-review.sh <group.json> <out.json>
#
# One read-only Claude pass over a group of PRs that implement one task, after
# each of them has been reviewed on its own. It looks only for problems between
# the PRs: contracts, migrations vs code, shared library versions, deploy order.
# group.json: {"task": "...", "prs": [{repo, number, title, url, worktree, merge_base, head_sha}]}
# The session gets the PR diffs as files and read access to every worktree; it
# has no shell, no edits, no gh, no web, no MCP servers and no GitHub token.
# Exit codes: 0 ok, 3 subscription/usage limit hit (stop the batch), 1 other error.
set -uo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
source "$S/common.sh"
SPEC=$1; OUT=$2
EMPTY_GH="$STATE_DIR/empty-gh-config"; mkdir -p "$EMPTY_GH"
WORK="${OUT%.json}.d"; rm -rf "$WORK"; mkdir -p "$WORK"

ADD_DIRS=()
while IFS=$'\t' read -r name wt mb head; do
  git -C "$wt" diff "$mb...$head" > "$WORK/$name.diff" || die "cannot diff $wt"
  ADD_DIRS+=(--add-dir "$wt")
done < <(python3 -c '
import json, sys
for p in json.load(open(sys.argv[1]))["prs"]:
    print("\t".join([p["repo"].split("/")[-1] + "-" + str(p["number"]), p["worktree"], p["merge_base"], p["head_sha"]]))
' "$SPEC")

PROMPT=$(python3 - "$SPEC" <<'PY'
import json, sys
spec = json.load(open(sys.argv[1]))
lines = []
for p in spec["prs"]:
    name = p["repo"].split("/")[-1] + "-" + str(p["number"])
    lines.append(f"- {p['repo']}#{p['number']} \"{p['title']}\": diff in ./{name}.diff, "
                 f"full checkout at {p['worktree']} (head {p['head_sha'][:10]})")
print(f"""These pull requests implement one task ({spec['task']}) across repositories:
{chr(10).join(lines)}

Each PR has already been reviewed on its own. Do not repeat findings that are visible within a single PR.
Look only for problems that appear when these PRs work together:
- producer/consumer contracts: event and DTO fields, JSON property names, types, nullability, enum values,
  topic/queue names, HTTP paths, methods, request/response shapes, status codes;
- DB migrations vs entities, DAO queries and column types; backfills the code relies on;
- config property names and defaults that one side sets and the other reads;
- shared library changes: a service using a class, method or field the library PR does not provide,
  or depending on a library version that is not released or not bumped;
- deploy order: a consumer or reader that breaks if it ships before its producer, migration or library;
- an assumption one PR makes about another PR's behaviour that the other PR does not hold.

Verify each finding by reading the code on both sides. Drop anything you could not tie to a concrete line.
Output one line per finding, no preamble:
<owner/repo>#<number> <path>:<line> — <what breaks and when> (other side: <owner/repo>#<number> <path>:<line>) [bug|minor] [verified|likely]
Use the new-file line of the PR where the fix belongs. If nothing is found, answer exactly: No cross-PR issues found.""")
PY
)

cd "$WORK" || die "no dir $WORK"
env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR="$EMPTY_GH" \
  claude -p "$PROMPT" --model opus "${ADD_DIRS[@]}" \
  --output-format json --setting-sources project --permission-mode default --no-session-persistence \
  --strict-mcp-config \
  --allowedTools Read Grep Glob Agent Task \
  --disallowedTools Bash Edit Write NotebookEdit WebFetch WebSearch \
  > "$OUT" 2> "$OUT.err"
rc=$?
python3 "$S/check-claude-output.py" "$OUT"
py=$?
[[ $py -ne 0 ]] && exit $py
exit $rc
