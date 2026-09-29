import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _THIS_DIR)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import helpers  # noqa: E402
from specguard import status  # noqa: E402


@contextlib.contextmanager
def _env(**kwargs):
    old = {k: os.environ.get(k) for k in kwargs}
    os.environ.update(kwargs)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TreeHashTests(unittest.TestCase):
    def test_tree_hash_stable_before_and_after_hook_run(self):
        before = status.compute_tree_hash(_REPO_ROOT)

        project_dir = tempfile.mkdtemp(prefix="specguard-status-")
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        helpers.make_project(project_dir, config={"version": 1})
        payload = helpers.pre_tool_use("s1", "Read", {"file_path": "a.py"})
        code, out, err = helpers.run_hook("PreToolUse", payload, project_dir)
        self.assertEqual(code, 0)

        after = status.compute_tree_hash(_REPO_ROOT)
        self.assertEqual(before, after)
        self.assertEqual(len(before), 64)


class CheckConfigTests(unittest.TestCase):
    def _run_check_config(self, project_dir):
        buf = io.StringIO()
        with _env(CLAUDE_PROJECT_DIR=project_dir), contextlib.redirect_stdout(buf):
            code = status.main(["--check-config"])
        return code, buf.getvalue()

    def test_rejects_stop_timeout_above_1800(self):
        project_dir = tempfile.mkdtemp(prefix="specguard-checkcfg-")
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        helpers.make_project(
            project_dir,
            config={
                "version": 1,
                "stop": {
                    "dirty_pathspec": ["*.ts"],
                    "fresh_paths": ["apps"],
                    "fresh_globs": ["*.ts"],
                    "exclude_dirs": ["node_modules"],
                    "run": "true",
                    "timeout": 1801,
                },
            },
        )
        code, output = self._run_check_config(project_dir)
        self.assertNotEqual(code, 0)
        self.assertIn("timeout", output)

    def test_accepts_valid_config(self):
        project_dir = tempfile.mkdtemp(prefix="specguard-checkcfg-ok-")
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        helpers.make_project(project_dir, config={"version": 1})
        code, output = self._run_check_config(project_dir)
        self.assertEqual(code, 0)
        self.assertIn("OK", output)


if __name__ == "__main__":
    unittest.main()
