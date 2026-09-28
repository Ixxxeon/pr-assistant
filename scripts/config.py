#!/usr/bin/env python3
"""pr-assistant settings.

  config.py get repos_dir           print the folder for repository clones; exit 4 if not set
  config.py set repos_dir <path>    save it
  config.py clone-dir <owner/repo>  print where this repo's clone is or will be, and whether it exists

repos_dir comes from $PR_ASSISTANT_REPOS_DIR, else from the config file
${XDG_CONFIG_HOME:-~/.config}/pr-assistant/config.json. There is no default:
the skills ask the user once and save the answer.

clone-dir reuses an existing clone only if its `origin` is the same GitHub repo:
<repos_dir>/<repo> first, then <repos_dir>/<owner>/<repo>. A new clone goes to
<repos_dir>/<repo>, or to <repos_dir>/<owner>/<repo> when <repo> is taken by
something else (another owner's repo with the same name, a fork, a plain folder).
Output of clone-dir: JSON {path, exists}.
"""
import json
import os
import re
import subprocess
import sys

NOT_CONFIGURED = 4
CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                      "pr-assistant", "config.json")


def load():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def repos_dir():
    value = os.environ.get("PR_ASSISTANT_REPOS_DIR") or load().get("repos_dir")
    return os.path.abspath(os.path.expanduser(value)) if value else None


def origin_repo(path):
    """owner/repo of the clone's origin on GitHub, lowercased, or None."""
    if not os.path.isdir(os.path.join(path, ".git")):
        return None
    r = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True)
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", r.stdout.strip())
    return m.group(1).lower() if m else None


def clone_dir(root, repo):
    owner, name = repo.split("/", 1)
    candidates = [os.path.join(root, name), os.path.join(root, owner, name)]
    for path in candidates:
        if origin_repo(path) == repo.lower():
            return path, True
    for path in candidates:
        if not os.path.exists(path):
            return path, False
    sys.exit(f"pr-assistant: {candidates[0]} and {candidates[1]} are both taken by something other than {repo}")


def main():
    args = sys.argv[1:]
    if args[:2] == ["get", "repos_dir"]:
        d = repos_dir()
        if not d:
            print(f"pr-assistant: folder for repository clones is not set. Ask the user where their "
                  f"repositories live (or should be cloned), then run: config.py set repos_dir <path>",
                  file=sys.stderr)
            sys.exit(NOT_CONFIGURED)
        print(d)
    elif args[:2] == ["set", "repos_dir"] and len(args) == 3:
        path = os.path.abspath(os.path.expanduser(args[2]))
        os.makedirs(path, exist_ok=True)
        data = load()
        data["repos_dir"] = path
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w") as f:
            json.dump(data, f, indent=1)
        print(f"repos_dir = {path} (saved to {CONFIG})")
    elif args[:1] == ["clone-dir"] and len(args) == 2:
        root = repos_dir()
        if not root:
            sys.exit(NOT_CONFIGURED)
        path, exists = clone_dir(root, args[1])
        print(json.dumps({"path": path, "exists": exists}))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
