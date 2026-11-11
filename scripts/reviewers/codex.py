"""Codex native review for a PR; ordinary exec for a cross-repository pass."""
import json

from reviewers import failed, check_final_text


def command(settings, kind, base, head, prompt, directories, gh_config):
    # Overrides are deliberately explicit: a user's interactive full-access
    # profile, MCP servers, apps and plugins must not leak into a review worker.
    args = ["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--json", "--sandbox", "read-only",
            "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
            "-c", "mcp_servers={}", "-c", "plugins={}", "-c", "projects={}",
            "-c", "features.apps=false", "-c", "features.plugins=false",
            "-c", "features.hooks=false", "-c", "features.shell_snapshot=false",
            "-c", 'shell_environment_policy.inherit="core"',
            "-c", 'shell_environment_policy.set.GH_CONFIG_DIR=' + json.dumps(gh_config),
            "-c", "shell_environment_policy.ignore_default_excludes=false",
            "-c", 'model_reasoning_effort=' + json.dumps(settings["effort"])]
    if settings["model"]:
        args += ["--model", settings["model"], "-c", "review_model=" + json.dumps(settings["model"])]
    if kind == "pr":
        args += ["review", "--base", base]
    else:
        # read-only permits reading the absolute worktree paths. --add-dir would
        # grant writes; it is intentionally not the counterpart of Claude's flag.
        args += ["--skip-git-repo-check", prompt]
    return args


def parse(raw, stderr, returncode):
    events = []
    try:
        for line in raw.splitlines():
            if line.strip():
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("event is not an object")
                events.append(event)
    except ValueError as error:
        raise failed(stderr or f"Codex returned invalid JSONL: {error}")
    failures = [e for e in events if e.get("type") == "turn.failed"]
    completed = [e for e in events if e.get("type") == "turn.completed"]
    if returncode or failures or not completed:
        errors = failures or [e for e in events if e.get("type") == "error"]
        raise failed(json.dumps(errors) if errors else stderr or "Codex did not complete the review")
    messages = [e["item"].get("text") for e in events
                if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
                and e["item"].get("type") == "agent_message"]
    result = messages[-1] if messages else None
    check_final_text(result)
    return {"result": result, "usage": completed[-1].get("usage", {})}
