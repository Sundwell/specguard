import os
import shutil
import sys
import tempfile
import time
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _THIS_DIR)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import helpers  # noqa: E402
from specguard import guard  # noqa: E402

PARFUME_CONFIG = {
    "version": 1,
    "repo": "sample-shops",
    "hidden": {"segments": ["src", "scripts"], "names": [], "paths": []},
    "tester_readable": ["apps/api/src/db/schema.ts"],
    "tests": {"segments": ["test", "e2e"], "paths": [], "file_regex": r"\.(test|spec)\.ts$"},
    "docs_dirs": ["docs"],
    "specs_dir": "docs/specs",
    "modes": {"default": "feature", "approval_default": True},
}


def _decision(project_dir, tool_name, tool_input, agent_type=None, state_home=None):
    payload = helpers.pre_tool_use("s1", tool_name, tool_input, agent_type=agent_type)
    code, out, err = helpers.run_hook("PreToolUse", payload, project_dir, state_home=state_home)
    if code != 0:
        raise AssertionError("hook exit {} stderr={}".format(code, err))
    decision = None
    if out:
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
    return decision, out, err


class RuleFiveBashTests(unittest.TestCase):
    """Every Bash example of plan.md rule 5, run as the tester role."""

    @classmethod
    def setUpClass(cls):
        cls.project_dir = tempfile.mkdtemp(prefix="specguard-r5-")
        helpers.make_project(cls.project_dir, config=PARFUME_CONFIG)
        cls.state_home = tempfile.mkdtemp(prefix="specguard-r5-state-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project_dir, ignore_errors=True)
        shutil.rmtree(cls.state_home, ignore_errors=True)

    DENIED_COMMANDS = [
        'ls sample-shops/apps/api/"src"',
        "cat apps/api/'src'/index.ts",
        'ls "src"',
        'ls packages/domain/"src"',
        'cat packages/domain/"src"/phone.ts',
        "cd apps/api && grep -rn x 'src'",
        "find . -path '*src*'",
        'bash -c "ls src"',
        "sh -c 'cd src && cat phone.ts'",
        "node -e \"require('fs').readdirSync('src')\"",
        "ls packages/domain/src/",
        'grep -rn "process.env" apps/api/test/ apps/api/src',
        'ps aux | grep -i "fake-services\\|apps/api/src/index"',
    ]

    ALLOWED_COMMANDS = [
        """cat sample-shops/package.json | grep -A5 '"scripts"'""",
    ]

    def test_denied_commands(self):
        for command in self.DENIED_COMMANDS:
            with self.subTest(command=command):
                decision, out, err = _decision(
                    self.project_dir, "Bash", {"command": command}, "tester", self.state_home
                )
                self.assertEqual(decision, "deny", "expected deny for {!r}, got {!r} ({})".format(command, decision, err))

    def test_allowed_commands(self):
        for command in self.ALLOWED_COMMANDS:
            with self.subTest(command=command):
                decision, out, err = _decision(
                    self.project_dir, "Bash", {"command": command}, "tester", self.state_home
                )
                self.assertIsNone(decision, "expected allow for {!r}, got {!r} ({})".format(command, decision, err))


class RuleOneProtectedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_dir = tempfile.mkdtemp(prefix="specguard-r1-")
        helpers.make_project(cls.project_dir, config={"version": 1})
        cls.state_home = tempfile.mkdtemp(prefix="specguard-r1-state-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project_dir, ignore_errors=True)
        shutil.rmtree(cls.state_home, ignore_errors=True)

    def _bash(self, command):
        return _decision(self.project_dir, "Bash", {"command": command}, None, self.state_home)

    def test_state_dir_default_spelling_denied(self):
        decision, _, _ = self._bash("cat ~/.local/state/specguard/log.jsonl")
        self.assertEqual(decision, "deny")

    def test_state_dir_home_var_denied(self):
        decision, _, _ = self._bash("cat $HOME/.local/state/specguard/log.jsonl")
        self.assertEqual(decision, "deny")

    def test_state_dir_xdg_var_denied(self):
        decision, _, _ = self._bash("cat $XDG_STATE_HOME/specguard/log.jsonl")
        self.assertEqual(decision, "deny")

    def test_state_dir_absolute_actual_path_denied(self):
        state_dir = os.path.join(
            self.state_home, "specguard", os.path.abspath(self.project_dir).replace(os.sep, "-")
        )
        decision, _, _ = self._bash("rm -rf {}/log.jsonl".format(state_dir))
        self.assertEqual(decision, "deny")

    def test_plugin_root_var_denied(self):
        decision, _, _ = self._bash('cat "$CLAUDE_PLUGIN_ROOT/scripts/specguard_hook.py"')
        self.assertEqual(decision, "deny")

    def test_plugin_root_absolute_denied(self):
        decision, _, _ = self._bash("cat {}/scripts/specguard_hook.py".format(_REPO_ROOT))
        self.assertEqual(decision, "deny")

    def test_status_invocation_allowed(self):
        command = 'python3 "{}/scripts/specguard_hook.py" --status'.format(_REPO_ROOT)
        decision, out, err = self._bash(command)
        self.assertIsNone(decision, err)

    def test_check_config_invocation_allowed(self):
        command = 'python3 "{}/scripts/specguard_hook.py" --check-config'.format(_REPO_ROOT)
        decision, out, err = self._bash(command)
        self.assertIsNone(decision, err)

    def test_write_into_plugin_root_denied(self):
        target = os.path.join(_REPO_ROOT, "templates", "x.md")
        decision, _, _ = _decision(
            self.project_dir, "Write", {"file_path": target, "content": "x"}, None, self.state_home
        )
        self.assertEqual(decision, "deny")

    def test_read_of_plugin_root_allowed(self):
        target = os.path.join(_REPO_ROOT, "README.md")
        decision, out, err = _decision(self.project_dir, "Read", {"file_path": target}, None, self.state_home)
        self.assertIsNone(decision, err)

    def test_write_into_state_dir_denied(self):
        state_dir = os.path.join(
            self.state_home, "specguard", os.path.abspath(self.project_dir).replace(os.sep, "-")
        )
        target = os.path.join(state_dir, "log.jsonl")
        decision, _, _ = _decision(
            self.project_dir, "Edit", {"file_path": target, "old_string": "a", "new_string": "b"}, None, self.state_home
        )
        self.assertEqual(decision, "deny")


class RuleTwoConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_dir = tempfile.mkdtemp(prefix="specguard-r2-")
        helpers.make_project(cls.project_dir, config={"version": 1})
        cls.state_home = tempfile.mkdtemp(prefix="specguard-r2-state-")
        cls.fake_home = tempfile.mkdtemp(prefix="specguard-r2-home-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project_dir, ignore_errors=True)
        shutil.rmtree(cls.state_home, ignore_errors=True)
        shutil.rmtree(cls.fake_home, ignore_errors=True)

    READONLY_REDIRECT_COMMANDS = [
        "cat .claude/settings.json 2>/dev/null | sort | uniq",
        "sed -n 1,5p .claude/specguard.json",
        "python3 fix.py && cat .claude/settings.json",
        "head -80 docs/СОСТОЯНИЕ.md; "
        "python3 shared/check-docs.py 2>&1 | tail -5; "
        "grep -rli \"админк\" docs .claude/specguard.json sample-shops/docs 2>/dev/null | head -20; "
        "cat .claude/specguard.json | head -40",
    ]

    NOT_READONLY_REDIRECT_COMMANDS = [
        "git diff .claude/settings.json > /tmp/x",
        "sed -n 'w out' .claude/specguard.json",
        "awk '{print}' .claude/settings.json",
        "cat .claude/settings.json | python3 -c \"open('.claude/settings.json','w')\"",
    ]

    def test_readonly_redirects_allowed_for_executor(self):
        for command in self.READONLY_REDIRECT_COMMANDS:
            with self.subTest(command=command):
                decision, _, err = _decision(self.project_dir, "Bash", {"command": command}, None, self.state_home)
                self.assertIsNone(decision, "expected allow for {!r}, got {!r} ({})".format(command, decision, err))

    def test_unsafe_redirects_still_ask_for_executor(self):
        for command in self.NOT_READONLY_REDIRECT_COMMANDS:
            with self.subTest(command=command):
                decision, _, err = _decision(self.project_dir, "Bash", {"command": command}, None, self.state_home)
                self.assertEqual(decision, "ask", "expected ask for {!r}, got {!r} ({})".format(command, decision, err))

    FILES = [
        lambda self: os.path.join(self.project_dir, ".claude", "specguard.json"),
        lambda self: os.path.join(self.project_dir, ".claude", "settings.json"),
        lambda self: os.path.join(self.project_dir, ".claude", "settings.local.json"),
        lambda self: os.path.join(self.fake_home, ".claude", "settings.json"),
    ]

    def test_file_tool_edit_asks_executor_denies_roles(self):
        for make_path in self.FILES:
            target = make_path(self)
            with self.subTest(target=target):
                decision, _, _ = _decision(
                    self.project_dir, "Edit", {"file_path": target, "old_string": "a", "new_string": "b"},
                    None, self.state_home,
                )
                self.assertEqual(decision, "ask")

                for agent_type in ("tester", "specguard:tester", "devils-advocate", "specguard:devils-advocate"):
                    decision, _, _ = _decision(
                        self.project_dir, "Edit", {"file_path": target, "old_string": "a", "new_string": "b"},
                        agent_type, self.state_home,
                    )
                    self.assertEqual(decision, "deny", agent_type)

    def test_bash_naming_settings_asks_executor_denies_roles(self):
        commands = [
            "rm .claude/settings.local.json",
        ]
        for command in commands:
            with self.subTest(command=command):
                decision, _, _ = _decision(self.project_dir, "Bash", {"command": command}, None, self.state_home)
                self.assertEqual(decision, "ask")
                decision, _, _ = _decision(
                    self.project_dir, "Bash", {"command": command}, "tester", self.state_home
                )
                self.assertEqual(decision, "deny")

    def test_readonly_bash_naming_settings_allowed_for_executor(self):
        commands = [
            "cat .claude/specguard.json",
            "cat .claude/settings.json",
            "cat ~/.claude/settings.json",
            "grep -n specguard /home/sundwell/.claude/settings.json",
            "cat .claude/specguard.json | jq .modes",
        ]
        for command in commands:
            with self.subTest(command=command):
                decision, _, err = _decision(self.project_dir, "Bash", {"command": command}, None, self.state_home)
                self.assertIsNone(decision, "expected allow for {!r}, got {!r} ({})".format(command, decision, err))

    def test_readonly_bash_naming_settings_still_denied_for_tester(self):
        decision, _, _ = _decision(
            self.project_dir, "Bash", {"command": "cat .claude/specguard.json"}, "tester", self.state_home
        )
        self.assertEqual(decision, "deny")

    def test_write_forms_naming_settings_still_ask_for_executor(self):
        commands = [
            "echo x > .claude/settings.local.json",
            "sed -i s/a/b/ .claude/specguard.json",
            "tee .claude/settings.json",
            "python3 -c \"open('.claude/settings.json','w')\"",
            "cp x .claude/settings.local.json",
            "find . -name settings.json -delete",
        ]
        for command in commands:
            with self.subTest(command=command):
                decision, _, err = _decision(self.project_dir, "Bash", {"command": command}, None, self.state_home)
                self.assertEqual(decision, "ask", "expected ask for {!r}, got {!r} ({})".format(command, decision, err))


class BashIsReadonlyTests(unittest.TestCase):
    """Direct checks on the redirection-aware read-only detector used by rule 2."""

    READONLY_COMMANDS = [
        "ls sample-shops sample-shops/docs/specs 2>&1 | head -40; echo ---; "
        "git diff .claude/settings.json | head -60; echo ---; "
        "find . -path ./sample-shops -prune -o -path ./node_modules -prune -o "
        "\\( -name '*.test.*' -o -name '*.spec.*' \\) -print 2>/dev/null | grep -v node_modules | head -30",
        "cat .claude/settings.json 2>/dev/null | sort | uniq",
        "sed -n 1,5p .claude/specguard.json",
    ]

    NOT_READONLY_COMMANDS = [
        "git diff .claude/settings.json > /tmp/x",
        "echo x > .claude/settings.local.json",
        "sed -i s/a/b/ .claude/specguard.json",
        "sed -n 'w out' .claude/specguard.json",
        "ls .claude 2>&1 >/tmp/log",
        "awk '{print}' .claude/settings.json",
    ]

    def test_readonly_commands(self):
        for command in self.READONLY_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(guard._bash_is_readonly(command), command)

    def test_not_readonly_commands(self):
        for command in self.NOT_READONLY_COMMANDS:
            with self.subTest(command=command):
                self.assertFalse(guard._bash_is_readonly(command), command)


class RuleThreeNoChildClaudeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_dir = tempfile.mkdtemp(prefix="specguard-r3-")
        helpers.make_project(cls.project_dir, config={"version": 1})
        cls.state_home = tempfile.mkdtemp(prefix="specguard-r3-state-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project_dir, ignore_errors=True)
        shutil.rmtree(cls.state_home, ignore_errors=True)

    DENIED_COMMANDS = [
        "claude -p x",
        "/usr/local/bin/claude --agent specguard:tester",
        "npx @anthropic-ai/claude-code",
        "timeout 5 claude -p x",
        "env A=1 claude -p x",
        "echo hi | claude -p",
        "x=$(claude -p x)",
        'bash -c "cd /tmp && claude -p hi"',
    ]

    ALLOWED_COMMANDS = [
        "omc ask claude",
        "pgrep -af claude",
        "ps aux | grep claude",
        "grep -rn claude .",
        "ls .claude/",
    ]

    def test_denied_commands(self):
        for command in self.DENIED_COMMANDS:
            with self.subTest(command=command):
                decision, _, err = _decision(self.project_dir, "Bash", {"command": command}, None, self.state_home)
                self.assertEqual(decision, "deny", "{!r} -> {!r} ({})".format(command, decision, err))

    def test_allowed_commands(self):
        for command in self.ALLOWED_COMMANDS:
            with self.subTest(command=command):
                decision, _, err = _decision(self.project_dir, "Bash", {"command": command}, None, self.state_home)
                self.assertIsNone(decision, "{!r} -> {!r} ({})".format(command, decision, err))


class RuleFourLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_dir = tempfile.mkdtemp(prefix="specguard-r4-")
        helpers.make_project(cls.project_dir, config={"version": 1})
        cls.state_home = tempfile.mkdtemp(prefix="specguard-r4-state-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project_dir, ignore_errors=True)
        shutil.rmtree(cls.state_home, ignore_errors=True)

    def test_named_tester_launch_denied(self):
        ti = {"subagent_type": "specguard:tester", "description": "d", "prompt": "p", "name": "foo"}
        decision, _, _ = _decision(self.project_dir, "Agent", ti, None, self.state_home)
        self.assertEqual(decision, "deny")

    def test_team_tester_launch_denied(self):
        ti = {"subagent_type": "tester", "description": "d", "prompt": "p", "team_name": "x"}
        decision, _, _ = _decision(self.project_dir, "Agent", ti, None, self.state_home)
        self.assertEqual(decision, "deny")

    def test_isolated_advocate_launch_denied(self):
        ti = {"subagent_type": "specguard:devils-advocate", "description": "d", "prompt": "p", "isolation": "worktree"}
        decision, _, _ = _decision(self.project_dir, "Agent", ti, None, self.state_home)
        self.assertEqual(decision, "deny")

    def test_plain_tester_launch_allowed(self):
        ti = {"subagent_type": "specguard:tester", "description": "d", "prompt": "p"}
        decision, out, err = _decision(self.project_dir, "Agent", ti, None, self.state_home)
        self.assertIsNone(decision, err)

    def test_named_non_role_launch_allowed(self):
        ti = {"subagent_type": "some-other-agent", "description": "d", "prompt": "p", "name": "foo"}
        decision, out, err = _decision(self.project_dir, "Agent", ti, None, self.state_home)
        self.assertIsNone(decision, err)

    def test_skill_mode_scoped_denied(self):
        decision, _, _ = _decision(self.project_dir, "Skill", {"skill": "specguard:mode"}, None, self.state_home)
        self.assertEqual(decision, "deny")

    def test_skill_mode_bare_denied(self):
        decision, _, _ = _decision(self.project_dir, "Skill", {"skill": "mode"}, None, self.state_home)
        self.assertEqual(decision, "deny")

    def test_skill_other_allowed(self):
        decision, out, err = _decision(self.project_dir, "Skill", {"skill": "specguard:status"}, None, self.state_home)
        self.assertIsNone(decision, err)

    def test_skill_guide_allowed(self):
        decision, out, err = _decision(self.project_dir, "Skill", {"skill": "specguard:guide"}, None, self.state_home)
        self.assertIsNone(decision, err)


class ExecutorLockPerModeTests(unittest.TestCase):
    def _project(self, mode):
        project_dir = tempfile.mkdtemp(prefix="specguard-lock-{}-".format(mode))
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        config = {
            "version": 1,
            "repo": ".",
            "tests": {"segments": ["tests"], "paths": [], "file_regex": ""},
            "modes": {"default": mode},
        }
        helpers.make_project(project_dir, config=config)
        state_home = tempfile.mkdtemp(prefix="specguard-lock-{}-state-".format(mode))
        self.addCleanup(shutil.rmtree, state_home, ignore_errors=True)
        return project_dir, state_home

    def test_simple_mode_allows_executor_test_edit(self):
        project_dir, state_home = self._project("simple")
        target = os.path.join(project_dir, "tests", "foo_test.py")
        decision, out, err = _decision(
            project_dir, "Edit", {"file_path": target, "old_string": "a", "new_string": "b"}, None, state_home
        )
        self.assertIsNone(decision, err)

    def test_feature_mode_denies_executor_test_edit(self):
        project_dir, state_home = self._project("feature")
        target = os.path.join(project_dir, "tests", "foo_test.py")
        decision, _, _ = _decision(
            project_dir, "Edit", {"file_path": target, "old_string": "a", "new_string": "b"}, None, state_home
        )
        self.assertEqual(decision, "deny")

    def test_hard_mode_denies_executor_test_edit(self):
        project_dir, state_home = self._project("hard")
        target = os.path.join(project_dir, "tests", "foo_test.py")
        decision, _, _ = _decision(
            project_dir, "Edit", {"file_path": target, "old_string": "a", "new_string": "b"}, None, state_home
        )
        self.assertEqual(decision, "deny")


class MiscTests(unittest.TestCase):
    def test_allowed_call_prints_nothing(self):
        project_dir = tempfile.mkdtemp(prefix="specguard-misc-")
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        helpers.make_project(project_dir, config={"version": 1})
        state_home = tempfile.mkdtemp(prefix="specguard-misc-state-")
        self.addCleanup(shutil.rmtree, state_home, ignore_errors=True)

        payload = helpers.pre_tool_use("s1", "Read", {"file_path": os.path.join(project_dir, "a.py")})
        code, out, err = helpers.run_hook("PreToolUse", payload, project_dir, state_home=state_home)
        self.assertEqual(code, 0)
        self.assertIsNone(out)
        self.assertEqual(err, "")

    def test_pretooluse_fast_with_5000_files_under_node_modules(self):
        project_dir = tempfile.mkdtemp(prefix="specguard-perf-")
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        helpers.make_project(project_dir, config={"version": 1})
        state_home = tempfile.mkdtemp(prefix="specguard-perf-state-")
        self.addCleanup(shutil.rmtree, state_home, ignore_errors=True)

        node_modules = os.path.join(project_dir, "node_modules")
        os.makedirs(node_modules, exist_ok=True)
        for i in range(5000):
            open(os.path.join(node_modules, "file{}.js".format(i)), "w").close()

        payload = helpers.pre_tool_use(
            "s1", "Read", {"file_path": os.path.join(node_modules, "file2500.js")}
        )
        start = time.perf_counter()
        code, out, err = helpers.run_hook("PreToolUse", payload, project_dir, state_home=state_home)
        elapsed = time.perf_counter() - start
        self.assertEqual(code, 0, err)
        self.assertLess(elapsed, 0.2, "PreToolUse took {:.3f}s".format(elapsed))


if __name__ == "__main__":
    unittest.main()
