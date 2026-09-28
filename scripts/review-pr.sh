#!/usr/bin/env bash
# review-pr.sh <owner/repo> <number> [effort]
# Prepare the review worktree, run /code-review (or reuse the cached result for
# the same head SHA) and print JSON: worktree, merge_base, head_sha, url,
# review_file, review_text. Exit 3 = Claude usage limit hit; stop the batch.
# Exit 4 = the clone folder is not configured (config.py).
set -uo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
source "$S/common.sh"
REPO=$1; N=$2; EFFORT=${3:-medium}
WT_JSON=$(python3 "$S/locked.py" "$STATE_DIR/locks/${REPO/\//_}.lock" "$S/prepare-worktree.sh" review "$REPO" "$N") || exit $?
get() { python3 -c "import json,sys;print(json.loads(sys.argv[1])[sys.argv[2]])" "$WT_JSON" "$1"; }
WT=$(get worktree); MB=$(get merge_base); HEAD=$(get head_sha)
mkdir -p "$STATE_DIR/reviews"
OUT="$STATE_DIR/reviews/${REPO#*/}-$N-${HEAD:0:10}-$EFFORT.json"
if [[ ! -s $OUT ]]; then
  "$S/run-code-review.sh" "$WT" "$MB" "$HEAD" "$OUT" "$EFFORT"; rc=$?
  if [[ $rc -ne 0 ]]; then mv -f "$OUT" "$OUT.failed" 2>/dev/null; exit $rc; fi
else
  echo "cached review for ${HEAD:0:10}" >&2
fi
python3 - "$WT_JSON" "$OUT" <<'PY'
import json, sys
info = json.loads(sys.argv[1]); d = json.load(open(sys.argv[2]))
info.update(review_file=sys.argv[2], review_text=d.get("result", ""))
print(json.dumps(info, ensure_ascii=False, indent=1))
PY
