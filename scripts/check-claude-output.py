#!/usr/bin/env python3
"""check-claude-output.py <out.json>: validate a `claude -p --output-format json` result.
Exit codes: 0 ok, 3 subscription/usage limit hit (stop the batch), 1 other error."""
import json
import sys

try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"pr-assistant: review output unreadable: {e}", file=sys.stderr); sys.exit(1)
r = (d.get("result") or "").lower()
if ("hit your" in r and "limit" in r) or "usage limit" in r:
    print("pr-assistant: Claude usage limit reached; stop and retry after reset", file=sys.stderr); sys.exit(3)
if d.get("is_error"):
    print(f"pr-assistant: review failed: {d.get('result')}", file=sys.stderr); sys.exit(1)
tok = sum(m["inputTokens"] + m["outputTokens"] + m["cacheReadInputTokens"] + m["cacheCreationInputTokens"]
          for m in d.get("modelUsage", {}).values())
print(f"review done: {d.get('duration_ms', 0) / 1000:.0f}s, {tok / 1000:.0f}K tokens, ~${d.get('total_cost_usd', 0):.2f} API-equivalent", file=sys.stderr)
