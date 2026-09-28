# Shared settings for pr-assistant scripts (sourced).
# PR_ASSISTANT_STATE_DIR  review outputs, plans, hook state  (default ~/.cache/pr-assistant)
# Repository clones: see config.py (PR_ASSISTANT_REPOS_DIR or the config file; asked once, no default).
# Exit code 4 = the clone folder is not configured yet.
STATE_DIR="${PR_ASSISTANT_STATE_DIR:-$HOME/.cache/pr-assistant}"
mkdir -p "$STATE_DIR"
die() { echo "pr-assistant: $*" >&2; exit 1; }
# Sets REPOS_DIR and WORKTREES_DIR, or exits 4 with a hint for the model.
need_repos_dir() {
  REPOS_DIR=$(python3 "$(dirname "${BASH_SOURCE[0]}")/config.py" get repos_dir) || exit 4
  WORKTREES_DIR="$REPOS_DIR/.pr-worktrees"
  mkdir -p "$WORKTREES_DIR"
}
