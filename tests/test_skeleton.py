import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _THIS_DIR)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import helpers  # noqa: E402

CONCURRENT_WORKER = """
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts_dir!r})
from specguard.state import State

state = State({project_dir!r})
worker = {worker!r}
for i in range(50):
    def fn(current, i=i, worker=worker):
        records = current.setdefault('records', [])
        records.append('{{}}-{{}}'.format(worker, i))
        return current
    state.update_json('concurrent.json', fn)
"""


class NoConfigTests(unittest.TestCase):
    def test_every_event_exits_zero_silently_without_config(self):
        project_dir = tempfile.mkdtemp(prefix="specguard-noconfig-")
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)

        events = [
            ("PreToolUse", helpers.pre_tool_use("s1", "Read", {"file_path": "a.py"})),
            ("UserPromptSubmit", helpers.user_prompt_submit("s1", "hello")),
            ("UserPromptExpansion", helpers.user_prompt_expansion("s1", "specguard:mode", "feature")),
            ("SessionStart", helpers.session_start("s1")),
            ("SubagentStart", helpers.subagent_start("s1", "a1", "specguard:tester")),
            ("PostToolUse", helpers.post_tool_use("s1", "AskUserQuestion", {}, {})),
            ("Stop", helpers.stop("s1")),
        ]
        for event, payload in events:
            with self.subTest(event=event):
                code, out, err = helpers.run_hook(event, payload, project_dir)
                self.assertEqual(code, 0)
                self.assertIsNone(out)
                self.assertEqual(err, "")


class FailSafeTests(unittest.TestCase):
    def setUp(self):
        self.tmp_plugin = tempfile.mkdtemp(prefix="specguard-plugin-")
        self.addCleanup(shutil.rmtree, self.tmp_plugin, ignore_errors=True)
        for name in ("scripts", "hooks", ".claude-plugin"):
            shutil.copytree(os.path.join(_REPO_ROOT, name), os.path.join(self.tmp_plugin, name))
        core_path = os.path.join(self.tmp_plugin, "scripts", "specguard", "core.py")
        with open(core_path, "w", encoding="utf-8") as f:
            f.write("raise RuntimeError('boom, core.py is broken for this test')\n")
        self.hook_path = os.path.join(self.tmp_plugin, "scripts", "specguard_hook.py")

        self.project_dir = tempfile.mkdtemp(prefix="specguard-project-")
        self.addCleanup(shutil.rmtree, self.project_dir, ignore_errors=True)
        helpers.make_project(self.project_dir, config={"version": 1})

        self.state_home = tempfile.mkdtemp(prefix="specguard-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)

    def _run(self, payload, session_id):
        return helpers.run_hook(
            "PreToolUse",
            payload,
            self.project_dir,
            state_home=self.state_home,
            hook_path=self.hook_path,
            plugin_root=self.tmp_plugin,
        )

    def test_tester_gets_deny_json(self):
        payload = helpers.pre_tool_use(
            "s-tester", "Read", {"file_path": "x.py"}, agent_type="specguard:tester"
        )
        code, out, err = self._run(payload, "s-tester")
        self.assertEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_executor_gets_only_system_message(self):
        payload = helpers.pre_tool_use("s-executor", "Read", {"file_path": "x.py"})
        code, out, err = self._run(payload, "s-executor")
        self.assertEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertIn("systemMessage", out)
        self.assertNotIn("hookSpecificOutput", out)
        self.assertNotIn("permissionDecision", json.dumps(out))

    def test_hard_mode_tester_launch_denied(self):
        state_dir = os.path.join(
            self.state_home, "specguard", os.path.abspath(self.project_dir).replace(os.sep, "-")
        )
        os.makedirs(os.path.join(state_dir, "sessions"), exist_ok=True)
        with open(os.path.join(state_dir, "sessions", "s-hard.json"), "w", encoding="utf-8") as f:
            json.dump({"modes": ["hard"]}, f)

        payload = helpers.pre_tool_use(
            "s-hard",
            "Agent",
            {"subagent_type": "specguard:tester", "description": "run tester"},
        )
        code, out, err = self._run(payload, "s-hard")
        self.assertEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")


class StateConcurrencyTests(unittest.TestCase):
    def test_two_processes_update_json_keep_all_records(self):
        project_dir = tempfile.mkdtemp(prefix="specguard-concurrent-")
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        state_home = tempfile.mkdtemp(prefix="specguard-state-")
        self.addCleanup(shutil.rmtree, state_home, ignore_errors=True)

        scripts_dir = os.path.join(_REPO_ROOT, "scripts")
        env = dict(os.environ)
        env["XDG_STATE_HOME"] = state_home
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        procs = []
        for worker in ("a", "b"):
            code = CONCURRENT_WORKER.format(scripts_dir=scripts_dir, project_dir=project_dir, worker=worker)
            procs.append(subprocess.Popen([sys.executable, "-B", "-c", code], env=env))

        for proc in procs:
            self.assertEqual(proc.wait(timeout=60), 0)

        old_xdg = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = state_home
        try:
            from specguard.state import State

            state = State(project_dir)
            data = state.read_json("concurrent.json", {})
        finally:
            if old_xdg is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = old_xdg

        self.assertEqual(len(data.get("records", [])), 100)
        self.assertEqual(len(set(data.get("records", []))), 100)


class ConfigValidateTests(unittest.TestCase):
    def test_rejects_stop_timeout_above_ceiling(self):
        from specguard import config

        raw = {
            "version": 1,
            "stop": {
                "dirty_pathspec": ["*.ts"],
                "fresh_paths": ["apps"],
                "fresh_globs": ["*.ts"],
                "exclude_dirs": ["node_modules"],
                "run": "true",
                "timeout": 1801,
            },
        }
        problems = config.validate(raw)
        self.assertTrue(any("timeout" in p for p in problems))

    def test_accepts_stop_timeout_at_ceiling(self):
        from specguard import config

        raw = {
            "version": 1,
            "stop": {
                "dirty_pathspec": ["*.ts"],
                "fresh_paths": ["apps"],
                "fresh_globs": ["*.ts"],
                "exclude_dirs": ["node_modules"],
                "run": "true",
                "timeout": 1800,
            },
        }
        problems = config.validate(raw)
        self.assertFalse(any("timeout" in p for p in problems))


class PathsTests(unittest.TestCase):
    def test_tokenize_keeps_quote_spans_and_operators(self):
        from specguard import paths

        command = """cat sample-shops/package.json | grep -A5 '"scripts"'"""
        tokens = paths.tokenize(command)
        self.assertIsNotNone(tokens)

        operators = [t for t in tokens if t.operator]
        self.assertTrue(any(t.raw == "|" for t in operators))

        quoted = [t for t in tokens if t.quoted]
        self.assertEqual(len(quoted), 1)
        self.assertEqual(quoted[0].raw, "'\"scripts\"'")
        self.assertEqual(quoted[0].text, '"scripts"')

    def test_classify_parfume_hidden_and_tester_readable(self):
        from specguard import config, paths

        raw = {
            "version": 1,
            "repo": "sample-shops",
            "hidden": {"segments": ["src", "scripts"], "names": [], "paths": []},
            "tester_readable": ["apps/api/src/db/schema.ts"],
            "tests": {"segments": ["test", "e2e"], "paths": [], "file_regex": r"\.(test|spec)\.ts$"},
            "docs_dirs": ["docs"],
            "specs_dir": "docs/specs",
        }
        cfg = config.Config(raw)
        cwd = "/home/sundwell/Other/flow-lab/parfume"

        info_index = paths.classify(cfg, "sample-shops/apps/api/src/index.ts", cwd)
        self.assertTrue(info_index.hidden)
        self.assertFalse(info_index.tester_readable)

        info_schema = paths.classify(cfg, "sample-shops/apps/api/src/db/schema.ts", cwd)
        self.assertTrue(info_schema.hidden)
        self.assertTrue(info_schema.tester_readable)


if __name__ == "__main__":
    unittest.main()
