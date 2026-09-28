#!/usr/bin/env bash
# review-pr.sh <owner/repo> <number> [effort] [since_sha]
# Prepare the review worktree, run /code-review (or reuse the cached result for
# the same range) and print JSON: worktree, merge_base, head_sha, url,
# range_base, incremental, review_file, review_text.
# With since_sha (your last reviewed head), only since_sha...head is reviewed,
# if since_sha is an ancestor of head and no merge commit came in after it;
# otherwise the whole PR (merge_base...head) is reviewed and a note says why.
# Exit 3 = Claude usage limit hit; stop the batch.
# Exit 4 = the clone folder is not configured (config.py).
set -uo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
source "$S/common.sh"
REPO=$1; N=$2; EFFORT=${3:-medium}; SINCE=${4:-}
WT_JSON=$(python3 "$S/locked.py" "$STATE_DIR/locks/${REPO/\//_}.lock" "$S/prepare-worktree.sh" review "$REPO" "$N") || exit $?
get() { python3 -c "import json,sys;print(json.loads(sys.argv[1])[sys.argv[2]])" "$WT_JSON" "$1"; }
WT=$(get worktree); MB=$(get merge_base); HEAD=$(get head_sha)
BASE=$MB; INCREMENTAL=false; NOTE=""
if [[ -n $SINCE ]]; then
  if ! git -C "$WT" cat-file -e "$SINCE^{commit}" 2>/dev/null \
      || ! git -C "$WT" merge-base --is-ancestor "$SINCE" "$HEAD"; then
    NOTE="last reviewed commit ${SINCE:0:10} is not in the PR history (force-push?); reviewing the whole PR"
  elif [[ -n $(git -C "$WT" rev-list --merges "$SINCE..$HEAD") ]]; then
    NOTE="a merge came in after ${SINCE:0:10}; reviewing the whole PR to keep other people's changes out"
  else
    BASE=$SINCE; INCREMENTAL=true
  fi
fi
mkdir -p "$STATE_DIR/reviews"
SUFFIX=""; $INCREMENTAL && SUFFIX="-since-${BASE:0:10}"
OUT="$STATE_DIR/reviews/${REPO#*/}-$N-${HEAD:0:10}$SUFFIX-$EFFORT.json"
if [[ ! -s $OUT ]]; then
  "$S/run-code-review.sh" "$WT" "$BASE" "$HEAD" "$OUT" "$EFFORT"; rc=$?
  if [[ $rc -ne 0 ]]; then mv -f "$OUT" "$OUT.failed" 2>/dev/null; exit $rc; fi
else
  echo "cached review for ${BASE:0:10}...${HEAD:0:10}" >&2
fi
python3 - "$WT_JSON" "$OUT" "$BASE" "$INCREMENTAL" "$NOTE" <<'PY'
import json, sys
info = json.loads(sys.argv[1]); d = json.load(open(sys.argv[2]))
info.update(range_base=sys.argv[3], incremental=sys.argv[4] == "true",
            review_file=sys.argv[2], review_text=d.get("result", ""))
if sys.argv[5]:
    info["notes"] = info.get("notes", []) + [sys.argv[5]]
print(json.dumps(info, ensure_ascii=False, indent=1))
PY
