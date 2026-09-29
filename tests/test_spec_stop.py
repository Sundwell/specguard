import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402

NOTICE = {"systemMessage": "specguard: tester still running, tests run after it"}
TESTER_TASK = {"id": "a1", "type": "subagent", "status": "running", "agent_type": "specguard:tester"}


class Env:
    def __init__(self, case):
        self.project_dir = tempfile.mkdtemp(prefix="specguard-spec-stop-")
        self.state_home = tempfile.mkdtemp(prefix="specguard-spec-state-")
        self.aux = tempfile.mkdtemp(prefix="specguard-spec-aux-")
        for d in (self.project_dir, self.state_home, self.aux):
            case.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.runs = os.path.join(self.aux, "runs")

    def cmd(self, code, before=""):
        return "echo x >> {}; {}exit {}".format(self.runs, before, code)

    def build(self, run=None, modes_default="feature", stop=None, files=None, git=True,
              commit=True, tests=None, with_stop=True, config=None):
        if config is None:
            config = {"version": 1, "repo": ".", "modes": {"default": modes_default}}
            if tests is not None:
                config["tests"] = tests
            if with_stop:
                stop_cfg = {
                    "dirty_pathspec": ["."],
                    "fresh_paths": ["."],
                    "fresh_globs": ["*.py"],
                    "exclude_dirs": [".git", "node_modules"],
                    "skip_preset": "none",
                    "run": run if run is not None else self.cmd(1),
                    "timeout": 60,
                }
                stop_cfg.update(stop or {})
                config["stop"] = stop_cfg
        helpers.make_project(
            self.project_dir, config=config,
            files=files if files is not None else {"src/a.py": "x = 1\n"},
            git=git, commit=commit,
        )

    def write(self, rel, content="y = 2\n", mtime=None):
        full = os.path.join(self.project_dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        if mtime is not None:
            os.utime(full, (mtime, mtime))

    def stop(self, stop_hook_active=False, tasks=None, agent_type=None):
        payload = helpers.stop("s1", stop_hook_active=stop_hook_active,
                               background_tasks=tasks, agent_type=agent_type)
        return helpers.run_hook("Stop", payload, self.project_dir, state_home=self.state_home)

    def count(self):
        if not os.path.exists(self.runs):
            return 0
        with open(self.runs, encoding="utf-8") as f:
            return len(f.read().splitlines())

    def log(self):
        name = self.project_dir.replace("/", "-")
        path = os.path.join(self.state_home, "specguard", name, "log.jsonl")
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


class StopSpecTests(unittest.TestCase):
    def env(self):
        return Env(self)

    def assert_red_block(self, out):
        self.assertIsNotNone(out)
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].startswith("specguard: tests failed"), out["reason"])

    def test_ST_1_missing_config_prints_nothing(self):
        env = self.env()
        env.build(config=None, with_stop=False, files={"src/a.py": "x = 1\n"})
        os.remove(os.path.join(env.project_dir, ".claude", "specguard.json"))
        env.write("src/b.py")
        code, out, _ = env.stop()
        self.assertEqual(code, 0)
        self.assertIsNone(out)

    def test_ST_1_config_without_stop_run_prints_nothing(self):
        for label, kwargs in (
            ("no stop key", {"with_stop": False}),
            ("stop without run", {"config": {"version": 1, "repo": ".", "modes": {"default": "feature"},
                                              "stop": {"dirty_pathspec": ["."], "fresh_paths": ["."],
                                                       "fresh_globs": ["*.py"], "exclude_dirs": [".git"]}}}),
        ):
            with self.subTest(label):
                env = self.env()
                env.build(**kwargs)
                env.write("src/b.py")
                code, out, _ = env.stop()
                self.assertEqual(code, 0)
                self.assertIsNone(out)

    def test_ST_2_tester_and_devils_advocate_sessions_are_not_gated(self):
        for agent_type in ("specguard:tester", "tester", "specguard:devils-advocate", "devils-advocate"):
            with self.subTest(agent_type):
                env = self.env()
                env.build()
                env.write("src/b.py")
                code, out, _ = env.stop(agent_type=agent_type)
                self.assertEqual(code, 0)
                self.assertIsNone(out)
                self.assertEqual(env.count(), 0)

    def test_ST_2_tester_session_gets_no_notice_for_running_tester_task(self):
        env = self.env()
        env.build()
        env.write("src/b.py")
        code, out, _ = env.stop(agent_type="specguard:tester", tasks=[dict(TESTER_TASK)])
        self.assertEqual(code, 0)
        self.assertIsNone(out)
        self.assertEqual(env.count(), 0)

    def test_ST_3_stop_hook_active_skips_gate(self):
        env = self.env()
        env.build()
        env.write("src/b.py")
        code, out, _ = env.stop(stop_hook_active=True)
        self.assertEqual(code, 0)
        self.assertIsNone(out)
        self.assertEqual(env.count(), 0)

    def test_ST_4_running_tester_task_prints_notice_and_skips_run(self):
        for agent_type in ("specguard:tester", "tester"):
            with self.subTest(agent_type):
                env = self.env()
                env.build()
                env.write("src/b.py")
                task = dict(TESTER_TASK, agent_type=agent_type)
                code, out, _ = env.stop(tasks=[task])
                self.assertEqual(code, 0)
                self.assertEqual(out, NOTICE)
                self.assertEqual(env.count(), 0)

    def test_ST_4_tasks_that_do_not_count_leave_the_gate_running(self):
        rows = (
            ("shell type", {"type": "shell"}),
            ("teammate type", {"type": "teammate"}),
            ("done status", {"status": "done"}),
            ("non-tester agent_type", {"agent_type": "general-purpose"}),
        )
        for label, override in rows:
            with self.subTest(label):
                env = self.env()
                env.build()
                env.write("src/b.py")
                code, out, _ = env.stop(tasks=[dict(TESTER_TASK, **override)])
                self.assertEqual(code, 0)
                self.assert_red_block(out)
                self.assertEqual(env.count(), 1)

    def test_ST_4_task_without_agent_type_is_resolved_by_recorded_id(self):
        for recorded, expect_notice in (("specguard:tester", True), ("general-purpose", False)):
            with self.subTest(recorded):
                env = self.env()
                env.build()
                env.write("src/b.py")
                helpers.run_hook("SubagentStart", helpers.subagent_start("s1", "a1", recorded),
                                 env.project_dir, state_home=env.state_home)
                task = {"id": "a1", "type": "subagent", "status": "running"}
                code, out, _ = env.stop(tasks=[task])
                self.assertEqual(code, 0)
                if expect_notice:
                    self.assertEqual(out, NOTICE)
                    self.assertEqual(env.count(), 0)
                else:
                    self.assert_red_block(out)
                    self.assertEqual(env.count(), 1)

    def test_ST_4_notice_shows_on_clean_tree_in_simple_mode(self):
        env = self.env()
        env.build(modes_default="simple")
        code, out, _ = env.stop(tasks=[dict(TESTER_TASK)])
        self.assertEqual(code, 0)
        self.assertEqual(out, NOTICE)
        self.assertEqual(env.count(), 0)

    def test_ST_5_session_modes_against_stop_modes(self):
        rows = (
            ("simple", None, False),
            ("visual", None, False),
            ("feature", None, True),
            ("hard", None, True),
            ("simple", ["simple"], True),
            ("feature", ["hard"], False),
        )
        for mode, stop_modes, gated in rows:
            with self.subTest("{} stop.modes={}".format(mode, stop_modes)):
                env = self.env()
                env.build(modes_default=mode, stop={"modes": stop_modes} if stop_modes else None)
                env.write("src/b.py")
                code, out, _ = env.stop()
                self.assertEqual(code, 0)
                if gated:
                    self.assert_red_block(out)
                    self.assertEqual(env.count(), 1)
                else:
                    self.assertIsNone(out)
                    self.assertEqual(env.count(), 0)

    def test_ST_6_clean_tree_skips_gate(self):
        env = self.env()
        env.build()
        code, out, _ = env.stop()
        self.assertEqual(code, 0)
        self.assertIsNone(out)
        self.assertEqual(env.count(), 0)

    def test_ST_6_change_outside_pathspec_skips_gate(self):
        env = self.env()
        env.build(stop={"dirty_pathspec": ["src"]}, files={"src/a.py": "x = 1\n", "notes.txt": "a\n"})
        env.write("notes.txt", "changed\n")
        code, out, _ = env.stop()
        self.assertEqual(code, 0)
        self.assertIsNone(out)
        self.assertEqual(env.count(), 0)

    def test_ST_6_untracked_file_inside_pathspec_is_dirty(self):
        env = self.env()
        env.build(stop={"dirty_pathspec": ["src"]})
        env.write("src/new.py")
        code, out, _ = env.stop()
        self.assertEqual(code, 0)
        self.assert_red_block(out)
        self.assertEqual(env.count(), 1)

    def test_ST_6_non_git_folder_counts_as_dirty(self):
        env = self.env()
        env.build(git=False, commit=False)
        code, out, _ = env.stop()
        self.assertEqual(code, 0)
        self.assert_red_block(out)
        self.assertEqual(env.count(), 1)

    def test_ST_7_touch_after_green_decides_whether_gate_reruns(self):
        future = time.time() + 3600
        rows = (
            ("fresh py file", "src/a.py", {}, True),
            ("only a non-matching glob", "src/notes.md", {}, False),
            ("only inside excluded dir", "node_modules/x.py", {}, False),
            ("outside fresh_paths", "docs/a.py", {"fresh_paths": ["src"]}, False),
        )
        for label, rel, stop_over, reruns in rows:
            with self.subTest(label):
                env = self.env()
                env.build(run=env.cmd(0), stop=dict(stop_over, fresh_globs=["*.py"]))
                env.write("src/new.py")
                code, out, _ = env.stop()
                self.assertIsNone(out)
                self.assertEqual(env.count(), 1)
                env.write(rel, "z = 3\n", mtime=future)
                code, out, _ = env.stop()
                self.assertEqual(code, 0)
                self.assertIsNone(out)
                self.assertEqual(env.count(), 2 if reruns else 1)

    def test_ST_8_require_glob_decides_whether_gate_runs(self):
        for label, files, gated in (("no match", {}, False), ("recursive match", {"tests/unit/t.py": "t = 1\n"}, True)):
            with self.subTest(label):
                env = self.env()
                env.build(stop={"require_glob": "tests/**/*.py"})
                env.write("src/new.py")
                for rel, content in files.items():
                    env.write(rel, content)
                code, out, _ = env.stop()
                self.assertEqual(code, 0)
                if gated:
                    self.assert_red_block(out)
                    self.assertEqual(env.count(), 1)
                else:
                    self.assertIsNone(out)
                    self.assertEqual(env.count(), 0)

    def _skip_env(self, preset=None, skip_regex=None, rel="tests/a.test.py", content="",
                  skip_paths=None, file_regex=None, exclude_dirs=None):
        env = self.env()
        stop = {"skip_preset": preset or "none", "skip_paths": skip_paths or ["tests"]}
        if skip_regex:
            stop["skip_regex"] = skip_regex
        if exclude_dirs:
            stop["exclude_dirs"] = exclude_dirs
        tests = {"file_regex": file_regex} if file_regex else None
        env.build(stop=stop, tests=tests)
        env.write(rel, content)
        return env

    def test_ST_9_skip_hit_blocks_without_running(self):
        rows = (
            ("vitest", "it.skip('x', () => {})\n"),
            ("vitest", "it.only('x', () => {})\n"),
            ("vitest", "describe.todo('x')\n"),
            ("pytest", "@pytest.mark.skipif(True, reason='x')\n"),
            ("pytest", "raise unittest.SkipTest\n"),
            ("xunit", "[Ignore]\n"),
            ("xunit", '[Theory(Skip = "x")]\n'),
        )
        for preset, content in rows:
            with self.subTest("{} {}".format(preset, content.strip())):
                env = self._skip_env(preset=preset, content=content, file_regex=r"\.test\.py$")
                code, out, _ = env.stop()
                self.assertEqual(code, 0)
                self.assertEqual(out["decision"], "block")
                reason = out["reason"]
                self.assertTrue(reason.startswith("specguard: "), reason)
                self.assertNotIn("tests failed", reason)
                self.assertTrue(any(w in reason.lower() for w in ("skip", "focus", "todo")), reason)
                self.assertEqual(env.count(), 0)

    def test_ST_9_skip_regex_wins_over_preset(self):
        env = self._skip_env(preset="vitest", skip_regex="XXX_SKIP", content="# XXX_SKIP\n")
        code, out, _ = env.stop()
        self.assertEqual(out["decision"], "block")
        self.assertNotIn("tests failed", out["reason"])
        self.assertEqual(env.count(), 0)

    def test_ST_9_scan_misses_leave_gate_running(self):
        rows = (
            ("preset none", dict(preset="none", content="it.skip('x')\n")),
            ("preset of another stack", dict(preset="pytest", content="it.skip('x')\n")),
            ("skip_regex overrides preset", dict(preset="vitest", skip_regex="XXX_SKIP", content="it.skip('x')\n")),
            ("path fails tests.file_regex", dict(preset="vitest", rel="src/a.py", skip_paths=["."],
                                                 content="it.skip('x')\n", file_regex=r"\.test\.py$")),
            ("outside skip_paths", dict(preset="vitest", rel="other/a.test.py", content="it.skip('x')\n")),
            ("inside exclude_dirs", dict(preset="vitest", rel="tests/node_modules/a.test.py",
                                         content="it.skip('x')\n", exclude_dirs=[".git", "node_modules"])),
        )
        for label, kwargs in rows:
            with self.subTest(label):
                env = self._skip_env(**kwargs)
                code, out, _ = env.stop()
                self.assertEqual(code, 0)
                self.assert_red_block(out)
                self.assertEqual(env.count(), 1)

    def test_ST_10_green_run_prints_nothing_and_is_not_repeated(self):
        env = self.env()
        env.build(run=env.cmd(0))
        env.write("src/new.py")
        code, out, _ = env.stop()
        self.assertEqual(code, 0)
        self.assertIsNone(out)
        self.assertEqual(env.count(), 1)
        code, out, _ = env.stop()
        self.assertIsNone(out)
        self.assertEqual(env.count(), 1)

    def _red(self, before, stop=None):
        env = self.env()
        env.build(run=env.cmd(1, before), stop=stop)
        env.write("src/new.py")
        code, out, _ = env.stop()
        self.assertEqual(code, 0)
        self.assert_red_block(out)
        return env, out["reason"]

    def test_ST_11_reason_carries_only_matching_lines(self):
        _, reason = self._red('echo "FAIL a"; echo "ok b"; ')
        self.assertTrue(reason.endswith("FAIL a"), reason)
        self.assertNotIn("ok b", reason)
        self.assertIn("fix", reason.lower())

    def test_ST_11_reason_keeps_last_25_matching_lines(self):
        _, reason = self._red('for i in $(seq 1 30); do echo "FAIL $i"; done; ')
        head, tail = reason.split("\n\n", 1)
        self.assertEqual(tail.split("\n"), ["FAIL {}".format(n) for n in range(6, 31)])

    def test_ST_11_stderr_lines_are_included(self):
        _, reason = self._red('echo "FAIL from stderr" >&2; ')
        self.assertIn("FAIL from stderr", reason)

    def test_ST_11_no_output_leaves_only_first_sentence(self):
        _, reason = self._red("")
        self.assertNotIn("\n", reason.strip())

    def test_ST_11_summary_regex_replaces_default(self):
        _, reason = self._red('echo "boom 1"; echo "FAIL 2"; ', stop={"summary_regex": "boom"})
        self.assertIn("boom 1", reason)
        self.assertNotIn("FAIL 2", reason)

    def test_ST_11_red_run_is_not_remembered_as_green(self):
        env, _ = self._red("")
        code, out, _ = env.stop()
        self.assertEqual(code, 0)
        self.assert_red_block(out)
        self.assertEqual(env.count(), 2)

    def test_ST_12_timeout_blocks_and_returns_promptly(self):
        for run in ("sleep 30", "sleep 30; true"):
            with self.subTest(run):
                env = self.env()
                env.build(run=run, stop={"timeout": 1})
                env.write("src/new.py")
                started = time.monotonic()
                code, out, _ = env.stop()
                elapsed = time.monotonic() - started
                self.assertEqual(code, 0)
                self.assertEqual(out["decision"], "block")
                self.assertIn("stop.run timed out after 1s", out["reason"])
                self.assertLess(elapsed, 6)

    def test_ST_13_red_block_is_logged_as_stop_red(self):
        env, _ = self._red("")
        self.assertIn({"ev": "block", "rule": "stop-red"},
                      [{k: e.get(k) for k in ("ev", "rule")} for e in env.log()])

    def test_ST_13_skip_scan_block_is_logged(self):
        env = self._skip_env(preset="vitest", content="it.skip('x')\n")
        env.stop()
        self.assertIn({"ev": "block", "rule": "stop-skip-scan"},
                      [{k: e.get(k) for k in ("ev", "rule")} for e in env.log()])

    def test_ST_13_timeout_block_is_logged(self):
        env = self.env()
        env.build(run="sleep 30", stop={"timeout": 1})
        env.write("src/new.py")
        env.stop()
        self.assertIn({"ev": "block", "rule": "stop-timeout"},
                      [{k: e.get(k) for k in ("ev", "rule")} for e in env.log()])

    def test_ST_13_tester_notice_is_logged(self):
        env = self.env()
        env.build()
        env.write("src/new.py")
        env.stop(tasks=[dict(TESTER_TASK)])
        self.assertIn("stop-inflight", [e.get("ev") for e in env.log()])


if __name__ == "__main__":
    unittest.main()
