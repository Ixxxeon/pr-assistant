#!/usr/bin/env bash
# prepare-worktree.sh <review|own> <owner/repo> <number>
#
# Finds the repo's clone in the configured folder or clones it there (see
# config.py clone-dir), then prepares a separate git worktree for the PR in
# $REPOS_DIR/.pr-worktrees/<name>-<number>. Your own
# working copy, its branch and uncommitted changes are never touched.
#   review  detached at the PR head (read-only review of someone else's PR)
#   own     local branch pr-assistant/<number> tracking the PR branch, so fixes
#           can be committed and later pushed to it (push happens only via post.py)
# Output: JSON {repo_dir, worktree, base_ref, head_ref, head_sha, merge_base, url, notes}.
set -euo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
source "$S/common.sh"
need_repos_dir
MODE=$1; REPO=$2; N=$3
[[ $MODE == review || $MODE == own ]] || die "mode must be review or own"
NAME=${REPO#*/}
CLONE=$(python3 "$S/config.py" clone-dir "$REPO") || exit $?
RD=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['path'])" "$CLONE")
WT="$WORKTREES_DIR/$NAME-$N"
NOTES=()

META=$(gh pr view "$N" -R "$REPO" --json baseRefName,headRefName,headRefOid,url,isCrossRepository)
field() { python3 -c "import json,sys;print(json.loads(sys.argv[1])[sys.argv[2]])" "$META" "$1"; }
BASE_REF=$(field baseRefName); HEAD_REF=$(field headRefName); HEAD_SHA=$(field headRefOid); URL=$(field url)

if [[ ! -d $RD/.git ]]; then
  mkdir -p "$(dirname "$RD")"
  gh repo clone "$REPO" "$RD" -- -q >&2
  NOTES+=("cloned into $RD")
fi
git -C "$RD" fetch -q origin "+refs/heads/$BASE_REF:refs/remotes/origin/$BASE_REF" \
  "+refs/pull/$N/head:refs/remotes/origin/pr/$N" >&2

if [[ $MODE == review ]]; then
  if [[ -d $WT ]]; then
    [[ -z $(git -C "$WT" status --porcelain) ]] || die "worktree $WT has local changes; clean it or remove it"
    git -C "$WT" checkout -q --detach "$HEAD_SHA"
  else
    git -C "$RD" worktree add -q --detach "$WT" "$HEAD_SHA" >&2
  fi
else
  [[ $(field isCrossRepository) == False ]] || die "PR head is in a fork; own-PR mode supports same-repo branches only"
  git -C "$RD" fetch -q origin "+refs/heads/$HEAD_REF:refs/remotes/origin/$HEAD_REF" >&2
  LB="pr-assistant/$N"
  if [[ -d $WT ]]; then
    AHEAD=$(git -C "$WT" rev-list --count "origin/$HEAD_REF..HEAD")
    if [[ $AHEAD -gt 0 ]]; then
      NOTES+=("worktree has $AHEAD local commit(s) not on origin/$HEAD_REF; kept as is")
    elif [[ -n $(git -C "$WT" status --porcelain) ]]; then
      NOTES+=("worktree has uncommitted changes; kept as is")
    else
      git -C "$WT" merge -q --ff-only "origin/$HEAD_REF" >&2
    fi
  else
    git -C "$RD" worktree add -q -B "$LB" "$WT" "origin/$HEAD_REF" >&2
    git -C "$WT" branch -q --set-upstream-to="origin/$HEAD_REF" "$LB" >&2
  fi
fi

MB=$(git -C "$RD" merge-base "origin/$BASE_REF" "$(git -C "$WT" rev-parse HEAD)")
python3 - "$RD" "$WT" "$BASE_REF" "$HEAD_REF" "$(git -C "$WT" rev-parse HEAD)" "$MB" "$URL" "${NOTES[@]+"${NOTES[@]}"}" <<'PY'
import json, sys
a = sys.argv[1:]
print(json.dumps({"repo_dir": a[0], "worktree": a[1], "base_ref": a[2], "head_ref": a[3],
                  "head_sha": a[4], "merge_base": a[5], "url": a[6], "notes": a[7:]}, indent=1))
PY
