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


def _stop_config(run, timeout=300, skip_preset="none", skip_paths=None, modes_default="feature"):
    return {
        "version": 1,
        "repo": ".",
        "tests": {"segments": [], "paths": [], "file_regex": r"\.test\.py$"},
        "modes": {"default": modes_default},
        "stop": {
            "modes": ["feature", "hard"],
            "dirty_pathspec": ["*.py"],
            "fresh_paths": ["."],
            "fresh_globs": ["*.py"],
            "exclude_dirs": [".git"],
            "skip_preset": skip_preset,
            "skip_paths": skip_paths,
            "run": run,
            "summary_regex": "FAIL|Error",
            "timeout": timeout,
        },
    }


class StopTests(unittest.TestCase):
    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="specguard-stop-")
        self.addCleanup(shutil.rmtree, self.project_dir, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.project_dir, check=True, capture_output=True)

    def _init_repo(self, files):
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "specguard tests")
        for rel_path, content in files.items():
            full = os.path.join(self.project_dir, rel_path)
            os.makedirs(os.path.dirname(full) or self.project_dir, exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "initial")

    def _write_config(self, config):
        helpers.make_project(self.project_dir, config=config)

    def _run_stop(self, **stop_kwargs):
        payload = helpers.stop("s1", **stop_kwargs)
        return helpers.run_hook("Stop", payload, self.project_dir, state_home=self.state_home)

    def test_clean_tree_allows(self):
        self._init_repo({"a.py": "print('a')\n"})
        self._write_config(_stop_config(run="python3 -c \"import sys; sys.exit(1)\""))
        code, out, err = self._run_stop()
        self.assertEqual(code, 0)
        self.assertIsNone(out)

    def test_dirty_and_fresh_runs_gate_green(self):
        self._init_repo({"a.py": "print('a')\n"})
        with open(os.path.join(self.project_dir, "b.py"), "w", encoding="utf-8") as f:
            f.write("print('b')\n")
        self._write_config(_stop_config(run="python3 -c \"print('ok')\""))
        code, out, err = self._run_stop()
        self.assertEqual(code, 0)
        self.assertIsNone(out)

        from specguard.state import compute_state_dir

        state_dir = compute_state_dir(self.project_dir)
        old_home = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = self.state_home
        try:
            state_dir = compute_state_dir(self.project_dir)
        finally:
            if old_home is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = old_home
        self.assertTrue(os.path.exists(os.path.join(state_dir, "last-green")))

    def test_dirty_with_stale_marker_skips(self):
        self._init_repo({"a.py": "print('a')\n"})
        with open(os.path.join(self.project_dir, "b.py"), "w", encoding="utf-8") as f:
            f.write("print('b')\n")
        self._write_config(_stop_config(run="python3 -c \"import sys; sys.exit(1)\""))

        from specguard.state import compute_state_dir

        old_home = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = self.state_home
        try:
            state_dir = compute_state_dir(self.project_dir)
        finally:
            if old_home is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = old_home
        os.makedirs(state_dir, exist_ok=True)
        last_green = os.path.join(state_dir, "last-green")
        with open(last_green, "w", encoding="utf-8") as f:
            f.write("marker")
        future = os.path.getmtime(last_green) + 3600
        os.utime(last_green, (future, future))

        code, out, err = self._run_stop()
        self.assertEqual(code, 0)
        self.assertIsNone(out)

    def test_skip_scan_vitest_preset_blocks(self):
        self._init_repo({"a.py": "print('a')\n"})
        skipped_test = "a.test.py"
        with open(os.path.join(self.project_dir, skipped_test), "w", encoding="utf-8") as f:
            f.write("it.skip('broken', () => {})\n")
        with open(os.path.join(self.project_dir, "b.py"), "w", encoding="utf-8") as f:
            f.write("print('b')\n")
        self._write_config(_stop_config(run="python3 -c \"print('ok')\"", skip_preset="vitest"))
        code, out, err = self._run_stop()
        self.assertEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("skipped, focused or todo", out.get("reason", ""))

    def test_skip_scan_pytest_preset_blocks(self):
        # "@pytest.mark.skip" (no parens) is a positive line from seenby's own check-tests.sh regex.
        self._init_repo({"a.py": "print('a')\n"})
        skipped_test = "a.test.py"
        with open(os.path.join(self.project_dir, skipped_test), "w", encoding="utf-8") as f:
            f.write("@pytest.mark.skip\ndef test_thing():\n    pass\n")
        with open(os.path.join(self.project_dir, "b.py"), "w", encoding="utf-8") as f:
            f.write("print('b')\n")
        self._write_config(_stop_config(run="python3 -c \"print('ok')\"", skip_preset="pytest"))
        code, out, err = self._run_stop()
        self.assertEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("skipped, focused or todo", out.get("reason", ""))

    def test_skip_scan_xunit_preset_blocks(self):
        # "[Fact(Skip" is a positive line from snimak's own regex.
        self._init_repo({"a.py": "print('a')\n"})
        skipped_test = "a.test.py"
        with open(os.path.join(self.project_dir, skipped_test), "w", encoding="utf-8") as f:
            f.write("[Fact(Skip = \"broken\")]\npublic void Test() {}\n")
        with open(os.path.join(self.project_dir, "b.py"), "w", encoding="utf-8") as f:
            f.write("print('b')\n")
        self._write_config(_stop_config(run="python3 -c \"print('ok')\"", skip_preset="xunit"))
        code, out, err = self._run_stop()
        self.assertEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("skipped, focused or todo", out.get("reason", ""))

    def test_stop_hook_active_allows(self):
        self._write_config(_stop_config(run="python3 -c \"import sys; sys.exit(1)\""))
        code, out, err = self._run_stop(stop_hook_active=True)
        self.assertEqual(code, 0)
        self.assertIsNone(out)

    def test_role_session_allows(self):
        self._write_config(_stop_config(run="python3 -c \"import sys; sys.exit(1)\""))
        payload = helpers.stop("s1", agent_type="specguard:tester")
        code, out, err = helpers.run_hook("Stop", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(code, 0)
        self.assertIsNone(out)

    def test_tester_in_background_allows_with_message(self):
        self._write_config(_stop_config(run="python3 -c \"import sys; sys.exit(1)\""))
        background_tasks = [
            {"id": "a1", "type": "subagent", "status": "running", "description": "tester", "agent_type": "specguard:tester"}
        ]
        code, out, err = self._run_stop(background_tasks=background_tasks)
        self.assertEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertEqual(out.get("systemMessage"), "specguard: tester still running, tests run after it")

    def test_simple_mode_allows(self):
        self._write_config(_stop_config(run="python3 -c \"import sys; sys.exit(1)\"", modes_default="simple"))
        code, out, err = self._run_stop()
        self.assertEqual(code, 0)
        self.assertIsNone(out)

    def test_runner_timeout_blocks(self):
        self._init_repo({"a.py": "print('a')\n"})
        with open(os.path.join(self.project_dir, "b.py"), "w", encoding="utf-8") as f:
            f.write("print('b')\n")
        self._write_config(_stop_config(run="python3 -c \"import time; time.sleep(2)\"", timeout=1))
        code, out, err = self._run_stop()
        self.assertEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("timed out", out.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
