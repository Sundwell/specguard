import json
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402

BASE = {
    "version": 1,
    "repo": ".",
    "modes": {"default": "simple"},
    "hidden": {"segments": ["src"]},
    "tests": {"segments": ["test"]},
}

AGENT_TYPES = {
    "executor": None,
    "tester": "specguard:tester",
    "advocate": "specguard:devils-advocate",
}

HOOK_SCRIPT = os.path.join(helpers.REPO_ROOT, "scripts", "specguard_hook.py")


class Env:
    def __init__(self, case, config=None):
        self.project_dir = tempfile.mkdtemp(prefix="specguard-spec-gs-")
        self.state_home = tempfile.mkdtemp(prefix="specguard-spec-gs-state-")
        self.home = tempfile.mkdtemp(prefix="specguard-spec-gs-home-")
        for d in (self.project_dir, self.state_home, self.home):
            case.addCleanup(shutil.rmtree, d, ignore_errors=True)
        helpers.make_project(self.project_dir, config=dict(BASE if config is None else config))
        self.plugin_root = helpers.REPO_ROOT
        key = self.project_dir.replace("/", "-")
        self.state_dir = os.path.join(self.state_home, "specguard", key)

    def subst(self, text):
        return (text.replace("<state_dir>", self.state_dir)
                .replace("<plugin_root>", self.plugin_root)
                .replace("<home>", self.home))

    def call(self, tool, tool_input, role="executor"):
        extra = {}
        if AGENT_TYPES[role] is not None:
            extra["agent_type"] = AGENT_TYPES[role]
        payload = helpers.pre_tool_use("s1", tool, tool_input, **extra)
        code, out, _err = helpers.run_hook(
            "PreToolUse", payload, self.project_dir, state_home=self.state_home)
        assert code == 0
        return out

    def last_log(self):
        path = os.path.join(self.state_dir, "log.jsonl")
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        return json.loads(lines[-1])


def path_input(tool, path):
    if tool == "NotebookEdit":
        return {"notebook_path": path, "new_source": "x"}
    if tool == "Write":
        return {"file_path": path, "content": "x"}
    return {"file_path": path, "old_string": "a", "new_string": "b"}


class GuardCase(unittest.TestCase):
    def env(self, config=None):
        return Env(self, config)

    def expect(self, env, out, verdict, rule=None, role="executor", tool=None):
        """verdict is allow, deny or ask."""
        if verdict == "allow":
            self.assertIsNone(out)
            return
        self.assertIsNotNone(out)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], verdict)
        self.assertTrue(hso["permissionDecisionReason"])
        line = env.last_log()
        self.assertEqual(line["ev"], verdict)
        self.assertEqual(line["rule"], rule)
        self.assertEqual(line["role"], role)
        if tool is not None:
            self.assertEqual(line["tool"], tool)

    def bash_rows(self, rows):
        for command, verdict, rule, role in rows:
            with self.subTest(command=command, role=role):
                env = self.env()
                cmd = env.subst(command)
                out = env.call("Bash", {"command": cmd}, role=role)
                self.expect(env, out, verdict, rule, role, "Bash")


class TestRule1(GuardCase):
    def test_GS_1_write_tools_into_state_dir_or_plugin_root_are_denied(self):
        for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            for role in ("executor", "tester", "advocate"):
                for target in ("<state_dir>/sessions/s1.json",
                               "<plugin_root>/scripts/specguard/guard.py"):
                    with self.subTest(tool=tool, role=role, target=target):
                        env = self.env()
                        out = env.call(tool, path_input(tool, env.subst(target)), role=role)
                        self.expect(env, out, "deny", "1", role, tool)

    def test_GS_1_dotdot_spelling_resolving_into_state_dir_is_denied(self):
        env = self.env()
        os.makedirs(os.path.join(env.state_dir, "x"))
        out = env.call("Write", path_input("Write", env.state_dir + "/x/../sessions/s1.json"))
        self.expect(env, out, "deny", "1", "executor", "Write")

    def test_GS_1_dotdot_spelling_resolving_into_plugin_root_is_denied(self):
        env = self.env()
        target = env.plugin_root + "/scripts/../scripts/specguard/guard.py"
        out = env.call("Edit", path_input("Edit", target), role="tester")
        self.expect(env, out, "deny", "1", "tester", "Edit")

    def test_GS_1_relative_path_resolving_into_plugin_root_is_denied(self):
        env = self.env()
        rel = os.path.relpath(os.path.join(env.plugin_root, "hooks", "hooks.json"), env.project_dir)
        self.assertTrue(rel.startswith(".."))
        out = env.call("Write", path_input("Write", rel))
        self.expect(env, out, "deny", "1", "executor", "Write")

    def test_GS_1_relative_path_resolving_into_state_dir_is_denied(self):
        env = self.env()
        rel = os.path.relpath(os.path.join(env.state_dir, "sessions", "s1.json"), env.project_dir)
        self.assertTrue(rel.startswith(".."))
        out = env.call("MultiEdit", path_input("MultiEdit", rel))
        self.expect(env, out, "deny", "1", "executor", "MultiEdit")

    def test_GS_1_read_grep_glob_on_protected_paths_are_not_touched(self):
        rows = (
            ("Read", {"file_path": "<plugin_root>/README.md"}),
            ("Read", {"file_path": "<state_dir>/log.jsonl"}),
            ("Grep", {"pattern": "x", "path": "<plugin_root>"}),
            ("Grep", {"pattern": "x", "path": "<state_dir>"}),
            ("Glob", {"pattern": "*.py", "path": "<plugin_root>/scripts"}),
            ("Glob", {"pattern": "*", "path": "<state_dir>"}),
        )
        for tool, tool_input in rows:
            with self.subTest(tool=tool, tool_input=tool_input):
                env = self.env()
                filled = {k: env.subst(v) for k, v in tool_input.items()}
                self.assertIsNone(env.call(tool, filled))

    def test_GS_1_ordinary_file_write_is_allowed(self):
        env = self.env()
        self.assertIsNone(env.call("Write", path_input("Write", "notes/plan.md")))


class TestBashProtectedPaths(GuardCase):
    def test_GS_2_bash_containing_any_protected_path_spelling_is_denied(self):
        cmds = (
            "cat <state_dir>/log.jsonl",
            "cat <plugin_root>/README.md",
            "ls ~/.local/state/specguard",
            "ls $HOME/.local/state/specguard",
            "ls ${HOME}/.local/state/specguard",
            'rm -rf "$XDG_STATE_HOME/specguard"',
            "ls ${XDG_STATE_HOME}/specguard",
            "cat $CLAUDE_PLUGIN_ROOT/hooks/hooks.json",
            "cat ${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json",
        )
        self.bash_rows([(c, "deny", "1", "executor") for c in cmds])

    def test_GS_2_tester_and_advocate_are_denied_on_read_only_plugin_path(self):
        self.bash_rows([
            ("cat <plugin_root>/README.md", "deny", "1", "tester"),
            ("cat <state_dir>/log.jsonl", "deny", "1", "advocate"),
        ])

    def test_GS_2_command_without_protected_path_is_allowed(self):
        self.bash_rows([("ls notes", "allow", None, "executor")])

    def test_GS_3_status_and_check_config_forms_are_allowed(self):
        self.bash_rows([
            ("python3 <plugin_root>/scripts/specguard_hook.py --status", "allow", None, "executor"),
            ("python3 <plugin_root>/scripts/specguard_hook.py --check-config", "allow", None, "executor"),
        ])

    def test_GS_3_extra_word_second_command_or_missing_flag_brings_deny_back(self):
        h = "python3 <plugin_root>/scripts/specguard_hook.py"
        cmds = (
            h + " --status && cat <state_dir>/log.jsonl",
            h + " --status extra",
            h + " --check-config extra",
            h + " --status; ls",
            h + " --status || ls",
            h + " --status | cat",
            h + " --status\nls",
            h,
        )
        self.bash_rows([(c, "deny", "1", "executor") for c in cmds])


CFG_FILES = (".claude/specguard.json", ".claude/settings.json", ".claude/settings.local.json")


class TestRule2(GuardCase):
    def test_GS_4_write_tools_on_config_files_ask_the_executor(self):
        rows = (
            ("Write", ".claude/specguard.json"),
            ("Edit", ".claude/settings.json"),
            ("MultiEdit", ".claude/settings.local.json"),
            ("NotebookEdit", ".claude/settings.json"),
            ("Write", "<home>/.claude/settings.json"),
        )
        for tool, path in rows:
            with self.subTest(tool=tool, path=path):
                env = self.env()
                out = env.call(tool, path_input(tool, env.subst(path)))
                self.expect(env, out, "ask", "2", "executor", tool)

    def test_GS_4_write_tools_on_config_files_deny_tester_and_advocate(self):
        rows = (
            ("tester", "Write", ".claude/specguard.json"),
            ("advocate", "Edit", ".claude/settings.json"),
            ("tester", "MultiEdit", ".claude/settings.local.json"),
            ("advocate", "Write", "<home>/.claude/settings.json"),
        )
        for role, tool, path in rows:
            with self.subTest(role=role, tool=tool, path=path):
                env = self.env()
                out = env.call(tool, path_input(tool, env.subst(path)), role=role)
                self.expect(env, out, "deny", "2", role, tool)

    def test_GS_4_lookalike_paths_and_reads_are_not_touched_by_rule_2(self):
        rows = (
            ("Write", {"file_path": "config/specguard.json", "content": "x"}),
            ("Write", {"file_path": ".claude/notes.md", "content": "x"}),
            ("Write", {"file_path": "settings.json", "content": "x"}),
            ("Read", {"file_path": ".claude/specguard.json"}),
            ("Grep", {"pattern": "x", "path": ".claude/settings.json"}),
            ("Glob", {"pattern": "*.json", "path": ".claude"}),
        )
        for tool, tool_input in rows:
            with self.subTest(tool=tool, tool_input=tool_input):
                env = self.env()
                self.assertIsNone(env.call(tool, tool_input))

    def test_GS_5_executor_read_only_commands_naming_a_config_file_are_allowed(self):
        cmds = (
            "cat .claude/specguard.json",
            "grep -n hidden .claude/settings.json",
            "git diff .claude/settings.json",
            "cat .claude/specguard.json 2>&1",
            "cat .claude/settings.json 2>/dev/null",
            'bash -c "cat .claude/specguard.json"',
            "cat .claude/specguard.json; rm -rf build",
        )
        self.bash_rows([(c, "allow", None, "executor") for c in cmds])

    def test_GS_5_executor_writing_commands_naming_a_config_file_ask(self):
        cmds = (
            "echo x > .claude/specguard.json",
            "echo x >> .claude/settings.local.json",
            "sed -i s/a/b/ .claude/settings.json",
            "cp backup.json .claude/specguard.json",
            "echo x | tee .claude/specguard.json",
            "cat .claude/specguard.json && rm .claude/settings.json",
            'bash -c "echo x > .claude/specguard.json"',
            "sh -c 'echo x > .claude/settings.json'",
            "git checkout .claude/settings.json",
            "cat .claude/specguard.json > /tmp/copy.json",
        )
        self.bash_rows([(c, "ask", "2", "executor") for c in cmds])

    def test_GS_5_split_points_newline_or_and_dollar_paren_judge_each_simple_command(self):
        cmds = (
            "cat .claude/specguard.json\nrm .claude/settings.json",
            "cat .claude/specguard.json || rm .claude/settings.json",
            "cat .claude/specguard.json | tee .claude/settings.json",
            "echo $(rm .claude/settings.json)",
        )
        self.bash_rows([(c, "ask", "2", "executor") for c in cmds])

    def test_GS_5_tester_and_advocate_are_denied_even_for_read_only_commands(self):
        self.bash_rows([
            ("cat .claude/specguard.json", "deny", "2", "tester"),
            ("grep hidden .claude/settings.json", "deny", "2", "advocate"),
            ("ls .claude/settings.local.json", "deny", "2", "tester"),
        ])

    def test_GS_5_names_that_are_not_whole_words_are_not_checked(self):
        cmds = (
            "cat .claude/my-settings.json",
            "cat .claude/settings.json.bak",
            "cat .claude/settings.jsonl",
        )
        self.bash_rows([(c, "allow", None, "executor") for c in cmds])

    def test_GS_5_whole_word_boundary_accepts_quotes_and_slash(self):
        cmds = (
            'rm ".claude/settings.json"',
            "rm '.claude/specguard.json'",
        )
        self.bash_rows([(c, "ask", "2", "executor") for c in cmds])

    def test_GS_6_sed_jq_find_git_and_awk_follow_the_read_only_whitelist(self):
        rows = (
            ("sed -n 1,5p .claude/settings.json", "allow"),
            ("sed -ne 1,5p .claude/settings.json", "allow"),
            ("sed s/a/b/ .claude/settings.json", "ask"),
            ("sed -n 'w out.txt' .claude/settings.json", "ask"),
            ("sed -n 'W out.txt' .claude/settings.json", "ask"),
            ("sed -n -i 1p .claude/settings.json", "ask"),
            ("sed -n --in-place 1p .claude/settings.json", "ask"),
            ("jq . .claude/settings.json", "allow"),
            ("jq -i . .claude/settings.json", "ask"),
            ("find .claude -name settings.json", "allow"),
            ("find .claude -name settings.json -delete", "ask"),
            ("find .claude -name settings.json -exec rm {} ;", "ask"),
            ("find .claude -name settings.json -execdir rm {} ;", "ask"),
            ("find .claude -name settings.json -ok rm {} ;", "ask"),
            ("find .claude -name settings.json -fprint out.txt", "ask"),
            ("find .claude -name settings.json -fprintf out.txt x", "ask"),
            ("/bin/cat .claude/specguard.json", "allow"),
            ("git log -p .claude/settings.json", "allow"),
            ("git restore .claude/settings.json", "ask"),
            ("git add .claude/settings.json", "ask"),
            ("awk 1 .claude/settings.json", "ask"),
        )
        self.bash_rows([(c, v, None if v == "allow" else "2", "executor") for c, v in rows])

    def test_GS_6_every_listed_program_is_read_only(self):
        programs = ("cat less head tail grep rg ls stat wc diff file echo printf sort uniq "
                    "cut tr pwd cd basename dirname realpath readlink tree du true nl column "
                    "comm md5sum sha256sum").split()
        self.assertEqual(len(programs), 31)
        self.bash_rows([("{} .claude/settings.json".format(p), "allow", None, "executor")
                        for p in programs])

    def test_GS_6_listed_git_subcommands_are_read_only(self):
        subs = ("status", "log", "diff", "show", "blame", "grep", "ls-files")
        self.bash_rows([("git {} .claude/settings.json".format(s), "allow", None, "executor")
                        for s in subs])

    def test_GS_6_output_redirections_and_tee_make_it_not_read_only(self):
        cmds = (
            "cat .claude/settings.json > out.txt",
            "cat .claude/settings.json >> out.txt",
            "cat .claude/settings.json 2> err.txt",
            "cat .claude/settings.json &> all.txt",
            "tee -a .claude/settings.json",
        )
        self.bash_rows([(c, "ask", "2", "executor") for c in cmds])

    def test_GS_6_stderr_merges_and_dev_null_stay_read_only(self):
        cmds = (
            "cat .claude/settings.json >&2",
            "cat .claude/settings.json 1>&2",
            "cat .claude/settings.json > /dev/null",
            "cat .claude/settings.json 2>&1",
        )
        self.bash_rows([(c, "allow", None, "executor") for c in cmds])

    def test_GS_6_quoted_redirect_character_is_not_a_redirection(self):
        self.bash_rows([('grep ">" .claude/specguard.json', "allow", None, "executor")])


class TestRule3(GuardCase):
    def test_GS_7_claude_as_command_word_is_denied(self):
        cmds = (
            "claude",
            "claude --version",
            "/usr/local/bin/claude -p hi",
            "FOO=1 claude -p hi",
            "env FOO=1 claude",
            "timeout 5 claude -p hi",
            "nohup claude &",
            "sudo claude",
            "echo hi && claude",
            "echo hi | claude -p x",
            "echo hi || claude",
            "echo hi; claude",
            "sleep 1 & claude",
            "echo hi\nclaude",
            "echo $(claude --version)",
            "exec claude",
            "nice claude",
            "setsid claude",
            "command claude",
            "time claude",
            "ls | xargs claude",
            'bash -c "claude -p x"',
            "sh -c 'cd /tmp && claude'",
            "zsh -c 'claude'",
        )
        self.bash_rows([(c, "deny", "3", "executor") for c in cmds])

    def test_GS_7_package_name_anywhere_in_the_command_is_denied(self):
        self.bash_rows([
            ("npx @anthropic-ai/claude-code", "deny", "3", "executor"),
            ("echo @anthropic-ai/claude-code", "deny", "3", "executor"),
        ])

    def test_GS_7_tester_is_denied_too(self):
        self.bash_rows([("claude plugin list", "deny", "3", "tester")])

    def test_GS_8_claude_as_argument_or_part_of_a_word_is_allowed(self):
        cmds = (
            "echo claude",
            "grep claude README.md",
            "ls claude-notes",
            "cat claude.md",
            "omc ask claude",
            "git log --grep claude",
            "python3 run.py claude",
        )
        self.bash_rows([(c, "allow", None, "executor") for c in cmds])


class TestRule4(GuardCase):
    def agent(self, env, **fields):
        tool_input = {"description": "d", "prompt": "p"}
        tool_input.update(fields)
        return env.call("Agent", tool_input)

    def test_GS_9_named_team_or_isolated_role_launch_is_denied(self):
        rows = (
            {"subagent_type": "specguard:tester", "name": "t1"},
            {"subagent_type": "tester", "team_name": "core"},
            {"subagent_type": "specguard:devils-advocate", "isolation": "worktree"},
            {"subagent_type": "devils-advocate", "name": "adv"},
            {"subagent_type": "specguard:devils-advocate", "team_name": "core"},
            {"subagent_type": "tester", "isolation": "worktree"},
        )
        for fields in rows:
            with self.subTest(fields=fields):
                env = self.env()
                out = self.agent(env, **fields)
                self.expect(env, out, "deny", "4", "executor", "Agent")

    def test_GS_9_plain_role_launch_or_empty_fields_are_allowed(self):
        rows = (
            {"subagent_type": "specguard:tester"},
            {"subagent_type": "specguard:tester", "name": ""},
            {"subagent_type": "tester", "team_name": "", "isolation": ""},
            {"subagent_type": "devils-advocate"},
            {"subagent_type": "specguard:devils-advocate", "name": ""},
        )
        for fields in rows:
            with self.subTest(fields=fields):
                env = self.env()
                self.assertIsNone(self.agent(env, **fields))

    def test_GS_9_other_subagent_types_are_allowed_whatever_fields_they_carry(self):
        rows = (
            {"subagent_type": "general-purpose", "name": "helper"},
            {"subagent_type": "general-purpose", "team_name": "core", "isolation": "worktree"},
            {"subagent_type": "Explore", "name": "e1"},
        )
        for fields in rows:
            with self.subTest(fields=fields):
                env = self.env()
                self.assertIsNone(self.agent(env, **fields))

    def test_GS_10_mode_skill_is_denied_in_every_mode(self):
        for mode in ("simple", "feature"):
            for skill in ("specguard:mode", "mode"):
                with self.subTest(mode=mode, skill=skill):
                    config = dict(BASE, modes={"default": mode})
                    env = self.env(config)
                    out = env.call("Skill", {"skill": skill})
                    self.expect(env, out, "deny", "4", "executor", "Skill")

    def test_GS_10_other_skill_is_allowed(self):
        env = self.env()
        self.assertIsNone(env.call("Skill", {"skill": "specguard:guide"}))


if __name__ == "__main__":
    unittest.main()
