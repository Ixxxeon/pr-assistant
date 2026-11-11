"""Offline integration tests. Fake CLIs never connect to GitHub or model providers."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import approval
import guard
import reviewer
from reviewers import ReviewError, claude, codex


FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
if sys.argv[1:] == ["--version"]:
    print(os.environ.get("FAKE_VERSION", name + " 0.167.0-test"))
    sys.exit(0)
with open(os.environ["CALL_LOG"], "a") as f:
    f.write(json.dumps({"cli": name, "args": sys.argv[1:], "cwd": os.getcwd(),
                       "gh_token": os.environ.get("GH_TOKEN"), "nested": os.environ.get("CLAUDECODE"),
                       "gh_config": os.environ.get("GH_CONFIG_DIR")}) + "\n")
mode = os.environ.get("FAKE_MODE", "ok")
if name == "gh":
    print(json.dumps({"html_url": "https://example.invalid/review/1"}))
elif name == "claude":
    print(json.dumps({"result": "Usage limit reached" if mode == "limit" else "No issues found.",
                      "is_error": mode == "limit"}))
else:
    if mode == "limit":
        print(json.dumps({"type": "turn.failed", "error": {"message": "Usage limit reached"}}))
    else:
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "No issues found."}}))
        if mode != "truncated":
            print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 42}}))
'''


class Isolated(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pr-assistant-test-")
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        for name in ("claude", "codex", "gh"):
            p = self.bin / name
            p.write_text(FAKE_CLI.replace("#!/usr/bin/env python3", "#!" + sys.executable))
            p.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("PR_ASSISTANT_") and k not in ("CODEX_THREAD_ID", "CLAUDECODE")}
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        XDG_CONFIG_HOME=str(self.dir / "config"),
                        PR_ASSISTANT_STATE_DIR=str(self.dir / "state"),
                        PR_ASSISTANT_HOST="codex", PR_ASSISTANT_SESSION_ID="test-session",
                        CALL_LOG=str(self.dir / "calls"))
        self.patcher = patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.config_patcher = patch.object(reviewer.config, "CONFIG", str(self.dir / "config" / "settings.json"))
        self.config_patcher.start()
        self.addCleanup(self.config_patcher.stop)

    def call(self, *args, input=None, env=None):
        return subprocess.run([str(a) for a in args], input=input, capture_output=True, text=True,
                              env=env or dict(os.environ))

    def calls(self):
        p = self.dir / "calls"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def hook(self, mode, **event):
        event.setdefault("session_id", "test-session")
        r = self.call(sys.executable, ROOT / "scripts/guard.py", mode, "codex", input=json.dumps(event))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout else {}

    def git(self, *args):
        r = self.call("git", "-C", self.repo, *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def make_repo(self):
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "value.txt").write_text("before\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")
        (self.repo / "value.txt").write_text("after\n")
        self.git("commit", "-qam", "change")
        self.head = self.git("rev-parse", "HEAD")


class SelectionTests(Isolated):
    def test_auto_tracks_host_without_saving_first_choice(self):
        for host in ("claude", "codex", "claude"):
            os.environ["PR_ASSISTANT_HOST"] = host
            self.assertEqual(reviewer.settings()["engine"], host)
        self.assertFalse(Path(reviewer.config.CONFIG).exists())

    def test_override_and_model_precedence(self):
        reviewer.config.save_setting("reviewer", "claude")
        reviewer.config.save_setting("claude_model", "chosen-claude")
        self.assertEqual(reviewer.settings()["model"], "chosen-claude")
        os.environ["PR_ASSISTANT_REVIEWER"] = "codex"
        os.environ["PR_ASSISTANT_CODEX_MODEL"] = "chosen-codex"
        self.assertEqual(reviewer.settings()["model"], "chosen-codex")

    def test_unknown_or_ambiguous_host_never_uses_path_order(self):
        del os.environ["PR_ASSISTANT_HOST"]
        with self.assertRaises(ReviewError):
            reviewer.settings()
        os.environ["CODEX_THREAD_ID"] = "thread"
        self.assertEqual(reviewer.settings()["engine"], "codex")
        os.environ["CLAUDECODE"] = "1"
        with self.assertRaises(ReviewError):
            reviewer.settings()

    def test_missing_cli_does_not_fallback(self):
        with patch("reviewer.shutil.which", side_effect=lambda name: None if name == "codex" else "/fake/claude"):
            with self.assertRaises(ReviewError) as caught:
                reviewer.settings()
            self.assertEqual(caught.exception.code, 4)
        self.assertEqual(self.calls(), [])

    def test_old_codex_requires_update_before_review(self):
        os.environ["FAKE_VERSION"] = "codex-cli 0.100.0"
        with self.assertRaises(ReviewError) as caught:
            reviewer.settings()
        self.assertEqual(caught.exception.code, 4)
        self.assertEqual(self.calls(), [])


class OutputTests(unittest.TestCase):
    def test_empty_truncated_and_failed_are_not_clean_reviews(self):
        for raw in ("", "{}", '[]', '{"type":"turn.completed"}',
                    '{"type":"item.completed","item":{"type":"agent_message","text":"No issues"}}'):
            with self.subTest(raw=raw), self.assertRaises(ReviewError):
                codex.parse(raw, "", 0)
        for raw in ('{}', '[]', '{"result":""}', '{"result":"ok","is_error":true}'):
            with self.subTest(raw=raw), self.assertRaises(ReviewError):
                claude.parse(raw, "", 0)

    def test_limit_errors_are_classified(self):
        for parse, raw in ((codex.parse, '{"type":"turn.failed","error":{"message":"usage limit reached"}}'),
                           (claude.parse, '{"result":"You hit your usage limit","is_error":true}')):
            with self.assertRaises(ReviewError) as caught:
                parse(raw, "", 1)
            self.assertEqual(caught.exception.code, 3)

    def test_auth_error_with_success_envelope_is_not_a_review(self):
        # Observed in Claude CLI: exit 0 and no is_error flag, but expired OAuth.
        message = "Failed to authenticate: OAuth session expired and could not be refreshed"
        with self.assertRaises(ReviewError) as caught:
            claude.parse(json.dumps({"type": "result", "subtype": "success", "result": message}), "", 0)
        self.assertEqual(caught.exception.code, 4)
        with self.assertRaises(ReviewError):
            claude.parse('{"result":"Startup incomplete","num_turns":0,"is_error":false}', "", 0)
        with self.assertRaises(ReviewError):
            codex.parse('\n'.join(json.dumps(e) for e in (
                {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
                {"type": "turn.completed"})), "", 0)

    def test_rate_limit_bug_in_review_is_not_provider_failure(self):
        raw = '\n'.join(json.dumps(e) for e in (
            {"type": "item.completed", "item": {"type": "agent_message", "text": "The rate limit is not enforced."}},
            {"type": "turn.completed", "usage": {}}))
        self.assertIn("not enforced", codex.parse(raw, "", 0)["result"])


class WorkerTests(Isolated):
    def setUp(self):
        super().setUp()
        self.make_repo()

    def test_both_workers_use_same_result_and_clean_credentials(self):
        os.environ["GH_TOKEN"] = "fake-test-token"
        os.environ["CLAUDECODE"] = "1"
        for engine in ("claude", "codex"):
            os.environ["PR_ASSISTANT_HOST"] = engine
            out = self.dir / (engine + ".json")
            r = self.call("bash", ROOT / "scripts/run-code-review.sh", self.repo, self.base, self.head, out)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(out.read_text())
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["reviewer"]["engine"], engine)
        for call in self.calls():
            self.assertIsNone(call["gh_token"])
            self.assertIsNone(call["nested"])
        codex_call = self.calls()[-1]["args"]
        self.assertEqual(codex_call[-3:], ["review", "--base", self.base])
        self.assertIn("read-only", codex_call)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex_call)

    def test_failed_worker_does_not_create_cache(self):
        for mode, expected in (("limit", 3), ("truncated", 1)):
            os.environ["FAKE_MODE"] = mode
            out = self.dir / (mode + ".json")
            r = self.call("bash", ROOT / "scripts/run-code-review.sh", self.repo, self.base, self.head, out)
            self.assertEqual(r.returncode, expected, r.stderr)
            self.assertFalse(out.exists())

    def test_mismatched_head_does_not_launch_worker(self):
        r = self.call("bash", ROOT / "scripts/run-code-review.sh", self.repo, self.base, self.base,
                      self.dir / "bad.json")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_cross_review_reads_all_prs_without_codex_write_roots(self):
        spec = self.dir / "group.json"
        spec.write_text(json.dumps({"task": "TEST-1", "prs": [
            {"repo": f"owner/repo-{n}", "number": n, "worktree": str(self.repo),
             "merge_base": self.base, "head_sha": self.head} for n in (1, 2)]}))
        for engine in ("codex", "claude"):
            os.environ["PR_ASSISTANT_HOST"] = engine
            out = self.dir / (engine + "-cross.json")
            r = self.call("bash", ROOT / "scripts/cross-review.sh", spec, out, "high")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(out.read_text())["reviewer"]["effort"], "high")
            args = self.calls()[-1]["args"]
            if engine == "codex":
                self.assertNotIn("--add-dir", args)
                self.assertIn("owner/repo-2#2", args[-1])
            else:
                self.assertIn("--add-dir", args)

    def test_cache_separates_provider_model_effort_owner_and_base(self):
        plugin = self.dir / "plugin"
        shutil.copytree(ROOT / "scripts", plugin)
        prepare = plugin / "prepare-worktree.sh"
        prepare.write_text('#!' + sys.executable + '\nimport os\nprint(os.environ["WT_JSON"])\n')
        prepare.chmod(0o755)
        os.environ["WT_JSON"] = json.dumps({"worktree": str(self.repo), "merge_base": self.base,
                                             "head_sha": self.head, "url": "https://example.invalid/pr/1"})
        def run(owner="owner/repo", effort="medium"):
            r = self.call("bash", plugin / "review-pr.sh", owner, "1", effort)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)["review_file"]
        original = run()
        self.assertEqual(original, run())
        self.assertEqual(len(self.calls()), 1)
        os.environ["PR_ASSISTANT_HOST"] = "claude"
        self.assertNotEqual(original, run())
        os.environ["PR_ASSISTANT_HOST"] = "codex"
        os.environ["PR_ASSISTANT_CODEX_MODEL"] = "another-model"
        self.assertNotEqual(original, run())
        del os.environ["PR_ASSISTANT_CODEX_MODEL"]
        self.assertNotEqual(original, run(effort="high"))
        self.assertNotEqual(original, run(owner="another/repo"))
        os.environ["WT_JSON"] = json.dumps({"worktree": str(self.repo), "merge_base": self.head,
                                             "head_sha": self.head, "url": "https://example.invalid/pr/1"})
        self.assertNotEqual(original, run())


class ApprovalTests(Isolated):
    def setUp(self):
        super().setUp()
        self.plan = self.dir / "plan.json"
        self.plan.write_text(json.dumps({"reviews": [{"repo": "owner/repo", "number": 1,
            "commit_id": "a" * 40, "general": [{"body": "A test comment"}]}]}))
        os.utime(self.plan, (time.time() - 5, time.time() - 5))
        self.code = hashlib.sha256(self.plan.read_bytes()).hexdigest()[:6].upper()
        self.cmd = ("PR_ASSISTANT_SESSION_ID=test-session python3 " +
                    shlex.quote(str(ROOT / "scripts/post.py")) + " " + shlex.quote(str(self.plan)))

    def permit(self, prompt=None):
        self.hook("prompt", prompt=prompt or "ok " + self.code)
        return self.hook("pretool", tool_name="Bash", tool_input={"command": self.cmd})

    def post(self):
        return self.call(sys.executable, ROOT / "scripts/post.py", self.plan)

    def test_no_hook_receipt_means_no_github_write(self):
        r = self.post()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_approved_plan_sent_once(self):
        result = self.permit()
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(self.post().returncode, 0)
        count = len(self.calls())
        self.assertGreater(count, 0)
        self.assertNotEqual(self.post().returncode, 0)
        self.assertEqual(len(self.calls()), count)

    def test_edited_plan_or_new_prompt_invalidates_receipt(self):
        self.permit()
        self.plan.write_text(self.plan.read_text() + "\n")
        self.assertNotEqual(self.post().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_new_prompt_and_wrong_session_invalidate_receipt(self):
        self.permit()
        os.environ["PR_ASSISTANT_SESSION_ID"] = "another-session"
        self.assertNotEqual(self.post().returncode, 0)
        os.environ["PR_ASSISTANT_SESSION_ID"] = "test-session"
        self.hook("prompt", prompt="wait")
        self.assertNotEqual(self.post().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_negated_approval_does_not_authorize(self):
        result = self.permit("do not send ok " + self.code)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotEqual(self.post().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_commands_with_prefixes_and_substitutions_denied(self):
        self.hook("prompt", prompt="ok " + self.code)
        for cmd in ("echo hello; " + self.cmd, self.cmd + "; echo hello", self.cmd + " $(echo ignored)"):
            result = self.hook("pretool", tool_name="Bash", tool_input={"command": cmd})
            self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_source_edits_and_reads_are_not_post_invocations(self):
        for command in ("python3 - <<'PY'\nprint('pr-assistant post.py')\nPY",
                        "cat " + shlex.quote(str(ROOT / "scripts/post.py")),
                        "python3 /another/project/post.py input.json"):
            self.assertIsNone(guard.post_plan_arg(command))

    def test_doctor_checks_both_hooks_and_context_names_host(self):
        context = self.hook("start")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("PR_ASSISTANT_HOST=codex", context)
        self.assertIn("test-session", context)
        args = [sys.executable, ROOT / "scripts/doctor.py", "--review"]
        self.assertNotEqual(self.call(*args).returncode, 0)
        self.hook("prompt", prompt="review my PRs")
        self.hook("pretool", tool_name="Bash", tool_input={"command": shlex.join(map(str, args))})
        self.assertEqual(self.call(*args).returncode, 0)


if __name__ == "__main__":
    unittest.main()
