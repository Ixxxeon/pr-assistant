#!/usr/bin/env bash
# Your open PRs (you are the author). Output: JSON array of {repo, number, title, url, draft}.
set -euo pipefail
source "$(dirname "$0")/common.sh"
gh api -X GET search/issues --paginate \
  -f q='is:pr is:open archived:false author:@me' -f per_page=100 \
  --jq '[.items[] | {repo: (.repository_url | split("/") | .[-2:] | join("/")), number, title, url: .html_url, draft: .draft}]' \
  | python3 -c 'import json,sys; print(json.dumps([x for page in sys.stdin.read().split("\n") if page.strip() for x in json.loads(page)], ensure_ascii=False, indent=1))'
