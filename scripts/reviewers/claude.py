"""Claude Code /code-review and cross-PR review adapter."""
import json

from reviewers import ReviewError, failed, check_final_text


def command(settings, kind, base, head, prompt, directories, gh_config):
    text = f"/code-review {settings['effort']} {base}...{head}" if kind == "pr" else prompt
    args = ["claude", "-p", text, "--model", settings["model"], "--output-format", "json",
            "--setting-sources", "project", "--permission-mode", "default",
            "--no-session-persistence", "--strict-mcp-config"]
    for path in directories:
        args += ["--add-dir", path]
    allowed = ["Read", "Grep", "Glob", "Agent", "Task"]
    denied = ["Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"]
    if kind == "pr":
        allowed += ["Skill"] + [f"Bash({cmd}:*)" for cmd in (
            "git diff", "git log", "git show", "git blame", "git status", "git rev-parse",
            "git merge-base", "git ls-files", "ls", "rg", "grep", "find", "cat", "wc", "head", "sed")]
        denied += ["Bash(gh:*)", "Bash(git push:*)"]
    else:
        denied += ["Bash"]
    return args + ["--allowedTools", *allowed, "--disallowedTools", *denied]


def parse(raw, stderr, returncode):
    try:
        data = json.loads(raw)
    except ValueError:
        raise failed(stderr or "Claude returned invalid JSON")
    if not isinstance(data, dict):
        raise ReviewError("Claude returned a non-object result")
    result = data.get("result")
    if data.get("is_error") or str(data.get("subtype", "")).startswith("error") or returncode:
        raise failed(str(result or stderr or "Claude review failed"))
    if data.get("num_turns") == 0:
        raise failed(str(result or "Claude did not run a review turn"))
    # Some Claude versions return a usage-limit message without is_error.
    if isinstance(result, str) and ("hit your" in result.lower() and "limit" in result.lower()
                                  or result.lower().startswith("usage limit")):
        raise failed(result)
    check_final_text(result)
    return {"result": result, "usage": data.get("modelUsage", {}),
            "cost_usd": data.get("total_cost_usd")}
