#!/usr/bin/env bash
# run-code-review.sh <worktree> <base> <head_sha> <out.json> [effort]
# Exit codes: 0 complete, 3 usage limit, 4 setup needed, 1 other error.
set -euo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$S/reviewer.py" pr "$@"
