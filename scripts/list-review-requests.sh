#!/usr/bin/env bash
# Open PRs where review is requested from you personally (team-only requests excluded).
# Output: JSON array of {repo, number, title, author, url, draft, updated, head_ref,
# additions, deletions, changed_files, task}.
# `task` is the same for PRs that belong to one task (see group-prs.py), null otherwise.
set -euo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
source "$S/common.sh"
gh api graphql --paginate -F q='is:pr is:open archived:false user-review-requested:@me' -f query='
query($q: String!, $endCursor: String) {
  search(query: $q, type: ISSUE, first: 100, after: $endCursor) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      repository { nameWithOwner } number title url isDraft updatedAt headRefName body
      additions deletions changedFiles
      author { login } } }
  }
}' --jq '.data.search.nodes[] | select(.number) | {repo: .repository.nameWithOwner, number, title,
  author: .author.login, url, draft: .isDraft, updated: .updatedAt, head_ref: .headRefName,
  additions, deletions, changed_files: .changedFiles, body}' \
  | python3 "$S/group-prs.py"
