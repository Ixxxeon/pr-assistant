#!/usr/bin/env bash
# cross-review.sh <group.json> <out.json> [effort]
set -euo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$S/reviewer.py" cross "$@"
