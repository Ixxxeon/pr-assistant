#!/usr/bin/env python3
"""Select a reviewer, fingerprint its cache, and run a read-only CLI worker.

reviewer.py preflight [effort]        resolve auto and check the selected CLI
reviewer.py key [effort]              print the engine/model/prompt fingerprint
reviewer.py pr WORKTREE BASE HEAD OUT [effort]
reviewer.py cross SPEC OUT [effort]
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

import config
from reviewers import ReviewError

ROOT = Path(__file__).resolve().parent


def settings(effort="medium"):
    if effort not in ("low", "medium", "high"):
        raise ReviewError("effort must be low, medium or high")
    saved = config.load()
    engine = os.environ.get("PR_ASSISTANT_REVIEWER") or saved.get("reviewer", "auto")
    if engine == "auto":
        host = os.environ.get("PR_ASSISTANT_HOST")
        if not host:
            detected = []
            if os.environ.get("CODEX_THREAD_ID"):
                detected.append("codex")
            if os.environ.get("CLAUDECODE"):
                detected.append("claude")
            host = detected[0] if len(detected) == 1 else None
        if host not in ("claude", "codex"):
            raise ReviewError("cannot determine the host; the skill must set PR_ASSISTANT_HOST=claude or codex. "
                              "For terminal use, set PR_ASSISTANT_REVIEWER explicitly", 4)
        engine = host
    if engine not in ("claude", "codex"):
        raise ReviewError("reviewer must be auto, claude or codex")
    if not shutil.which(engine):
        raise ReviewError(f"{engine} CLI is not installed or not on PATH. Install and sign in to {engine}; "
                          "no other reviewer was started", 4)
    version = subprocess.run([engine, "--version"], capture_output=True, text=True, timeout=15)
    if version.returncode:
        raise ReviewError(f"cannot run {engine} --version: {version.stderr.strip()}", 4)
    numeric = re.search(r"\b(\d+)\.(\d+)\.(\d+)", version.stdout)
    if engine == "codex" and numeric and tuple(map(int, numeric.groups())) < (0, 167, 0):
        raise ReviewError("Codex CLI 0.167.0 or later is required; update Codex and retry", 4)
    model = os.environ.get(f"PR_ASSISTANT_{engine.upper()}_MODEL") or saved.get(f"{engine}_model")
    if not model and engine == "claude":
        model = "opus"
    if model is not None and not isinstance(model, str):
        raise ReviewError(f"{engine}_model must be a model id string", 4)
    # A code/prompt change invalidates cached reviews without manual version bumps.
    digest = hashlib.sha256()
    for path in [Path(__file__), ROOT / "reviewers" / "__init__.py",
                 ROOT / "reviewers" / f"{engine}.py", ROOT / "reviewers" / "cross_prompt.txt"]:
        digest.update(path.read_bytes())
    return {"engine": engine, "model": model, "effort": effort,
            "cli_version": version.stdout.strip(), "implementation": digest.hexdigest(), "schema": 1}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def verify_head(worktree, head):
    actual = subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True).strip()
    if actual != head:
        raise ReviewError(f"worktree HEAD changed: expected {head}, got {actual}")


def run(selected, kind, cwd, out, base=None, head=None, prompt=None, directories=()):
    adapter = importlib.import_module("reviewers." + selected["engine"])
    state = Path(os.environ.get("PR_ASSISTANT_STATE_DIR", "~/.cache/pr-assistant")).expanduser().resolve()
    empty_gh = state / "empty-gh-config"
    empty_gh.mkdir(parents=True, exist_ok=True)
    args = adapter.command(selected, kind, base, head, prompt, directories, str(empty_gh))
    env = os.environ.copy()
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(key, None)
    env["GH_CONFIG_DIR"] = str(empty_gh)
    env["PR_ASSISTANT_REVIEW_WORKER"] = "1"
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    # Only a validated final response becomes OUT; diagnostics are never cache hits.
    with open(str(out) + ".raw", "w") as raw, open(str(out) + ".err", "w") as err:
        process = subprocess.run(args, cwd=cwd, env=env, stdout=raw, stderr=err)
    data = adapter.parse(Path(str(out) + ".raw").read_text(), Path(str(out) + ".err").read_text(),
                         process.returncode)
    if kind == "pr":
        verify_head(cwd, head)
    data.update(status="ok", reviewer=selected, duration_ms=round((time.monotonic() - started) * 1000))
    with tempfile.NamedTemporaryFile("w", dir=out.parent, delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=1)
    os.replace(tmp.name, out)
    print(f"{selected['engine']} review done: {data['duration_ms'] / 1000:.0f}s", file=sys.stderr)


def cross(selected, spec_path, out):
    spec = json.loads(Path(spec_path).read_text())
    work = Path(str(Path(out).resolve()) + ".d")
    work.mkdir(parents=True, exist_ok=True)
    lines, directories = [], []
    for index, pr in enumerate(spec["prs"]):
        wt = str(Path(pr["worktree"]).resolve())
        verify_head(wt, pr["head_sha"])
        name = f"pr-{index}.diff"
        with (work / name).open("w") as diff:
            subprocess.run(["git", "-C", wt, "diff", f"{pr['merge_base']}...{pr['head_sha']}"],
                           stdout=diff, check=True)
        lines.append(f"- {pr['repo']}#{pr['number']} {json.dumps(pr.get('title', ''))}: diff in ./{name}, "
                     f"full checkout at {json.dumps(wt)} (head {pr['head_sha']})")
        directories.append(wt)
    prompt = (f"These pull requests implement one task ({spec['task']}) across repositories:\n"
              + "\n".join(lines) + "\n\n" + (ROOT / "reviewers" / "cross_prompt.txt").read_text())
    run(selected, "cross", work, out, prompt=prompt, directories=directories)
    for pr in spec["prs"]:
        try:
            verify_head(pr["worktree"], pr["head_sha"])
        except ReviewError:
            Path(out).unlink(missing_ok=True)
            raise


def main():
    args = sys.argv[1:]
    try:
        if args and args[0] in ("preflight", "key") and len(args) in (1, 2):
            selected = settings(args[1] if len(args) == 2 else "medium")
            print(fingerprint(selected) if args[0] == "key" else json.dumps(selected))
        elif args and args[0] == "pr" and len(args) in (5, 6):
            _, wt, base, head, out, *effort = args
            selected = settings(effort[0] if effort else "medium")
            verify_head(wt, head)
            run(selected, "pr", wt, out, base=base, head=head)
        elif args and args[0] == "cross" and len(args) in (3, 4):
            cross(settings(args[3] if len(args) == 4 else "medium"), args[1], args[2])
        else:
            raise ReviewError(__doc__)
    except ReviewError as error:
        print(f"pr-assistant: {error}", file=sys.stderr)
        return error.code
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"pr-assistant: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
