import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402

INTRO = (
    "This project runs the specguard plugin (role-separated TDD). "
    "For how it works, load the specguard:guide skill; do not search the disk for it."
)
SUBAGENT_TAIL = "Only the main session can ask the user; report gate refusals to it."
SID = "s1"
TESTER_NOTES_PATH = ".claude/specguard/tester-notes.md"
ADVOCATE_NOTES_PATH = ".claude/specguard/advocate-notes.md"


def cfg(default="feature", approval=False, **extra):
    c = {"version": 1, "modes": {"default": default, "approval_default": approval}}
    c.update(extra)
    return c


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sg-ctx-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = os.path.join(self.tmp, "state")
        os.makedirs(self.state_home)
        self.project = None

    def build(self, config, files=None, git=False, commit=False):
        self.project = helpers.make_project(
            os.path.join(self.tmp, "proj"), config=config, files=files, git=git, commit=commit
        )
        return self.project

    def send(self, event, payload):
        code, out, _err = helpers.run_hook(event, payload, self.project, state_home=self.state_home)
        self.assertEqual(code, 0)
        return out

    def text(self, event, payload):
        out = self.send(event, payload)
        self.assertIsNotNone(out)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], event)
        return out["hookSpecificOutput"]["additionalContext"]

    def session_start(self, source="startup", **extra):
        return self.text("SessionStart", helpers.session_start(SID, source=source, **extra))

    def prompt(self, **extra):
        return self.text("UserPromptSubmit", helpers.user_prompt_submit(SID, "hello", **extra))

    def subagent(self, agent_type, agent_id="a1"):
        return self.text("SubagentStart", helpers.subagent_start(SID, agent_id, agent_type))

    def switch(self, modes):
        self.send("UserPromptExpansion", helpers.user_prompt_expansion(SID, "specguard:mode", modes))

    def open_request(self, spec):
        """Refused tester launch on an uncommitted spec, needs approval_default true."""
        full = os.path.join(self.project, spec)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write("# spec\n")
        self.send(
            "PreToolUse",
            helpers.pre_tool_use(
                SID,
                "Agent",
                {"subagent_type": "specguard:tester", "prompt": "Write tests for " + spec},
            ),
        )

    def build_approval_project(self, default="feature"):
        return self.build(
            cfg(default, True), files={"README.md": "x\n"}, git=True, commit=True
        )


class TestCX1NoConfig(Base):
    def test_CX_1_no_config_prints_nothing(self):
        cases = [
            ("SessionStart", helpers.session_start(SID)),
            ("UserPromptSubmit", helpers.user_prompt_submit(SID, "hello")),
            ("SubagentStart", helpers.subagent_start(SID, "a1", "general-purpose")),
        ]
        for event, payload in cases:
            with self.subTest(event=event):
                self.build(None)
                self.assertIsNone(self.send(event, payload))


class TestCX2SessionStart(Base):
    def test_CX_2_simple_has_intro_and_confirmation_rule_only(self):
        self.build(cfg("simple"))
        t = self.session_start()
        self.assertTrue(t.startswith(INTRO))
        self.assertIn("specguard-mode <set>", t)
        self.assertIn("AskUserQuestion", t)
        self.assertIn("do not ask him to rephrase", t)
        self.assertNotIn("Approval is", t)
        self.assertNotIn("rules.", t)

    def test_CX_2_intro_comes_before_the_approval_line_in_feature(self):
        self.build(cfg("feature"))
        t = self.session_start()
        self.assertTrue(t.startswith(INTRO))
        self.assertIn("specguard-mode <set>", t)
        self.assertLess(t.index(INTRO), t.index("Approval is"))
        self.assertLess(t.index("specguard-mode <set>"), t.index("Approval is"))

    def test_CX_2_resume_after_switch_prints_the_switched_rules(self):
        self.build(cfg("feature"))
        self.switch("visual+hard")
        t = self.session_start(source="resume")
        self.assertIn("Hard mode adds.", t)
        self.assertIn("Visual mode rules.", t)

    def test_CX_2_resume_after_switch_to_hard(self):
        self.build(cfg("simple"))
        self.switch("hard")
        t = self.session_start(source="resume")
        self.assertIn("Hard mode adds.", t)


class TestCX3ModeRules(Base):
    FEATURE_PHRASES = [
        "Feature mode rules.",
        "Write the spec with behaviour and rule IDs",
        "Never paste implementation code into the tester's task",
        "Talk to the user in the user's language.",
    ]
    HARD_PHRASES = ["Hard mode adds.", "devils-advocate", "mandatory", "manual mutation check", "at least three"]
    VISUAL_PHRASES = ["Visual mode rules.", "DESIGN | NOW", "DESIGN | BEFORE | NOW", "deviations log", "what I decided myself"]

    def test_CX_3_feature_approval_off(self):
        self.build(cfg("feature", False))
        t = self.session_start()
        self.assertIn("Approval is off.", t)
        for p in self.FEATURE_PHRASES:
            self.assertIn(p, t)
        self.assertNotIn("Hard mode adds.", t)
        self.assertNotIn("Visual mode rules.", t)
        self.assertNotIn("Approval is on.", t)

    def test_CX_3_feature_approval_on(self):
        self.build(cfg("feature", True))
        t = self.session_start()
        self.assertIn("Approval is on.", t)
        self.assertNotIn("Approval is off.", t)

    def test_CX_3_hard_is_always_approval_on_with_hard_paragraph(self):
        self.build(cfg("hard", False))
        t = self.session_start()
        self.assertIn("Approval is on.", t)
        self.assertNotIn("Approval is off.", t)
        for p in self.FEATURE_PHRASES + self.HARD_PHRASES:
            self.assertIn(p, t)
        self.assertLess(t.index("Feature mode rules."), t.index("Hard mode adds."))

    def test_CX_3_visual_alone_has_no_approval_line(self):
        self.build(cfg("visual", True))
        t = self.session_start()
        for p in self.VISUAL_PHRASES:
            self.assertIn(p, t)
        self.assertNotIn("Approval is", t)
        self.assertNotIn("Feature mode rules.", t)
        self.assertNotIn("Hard mode adds.", t)

    def test_CX_3_visual_feature_order(self):
        self.build(cfg("feature", False))
        self.switch("visual+feature")
        t = self.session_start(source="resume")
        self.assertLess(t.index("Approval is"), t.index("Feature mode rules."))
        self.assertLess(t.index("Feature mode rules."), t.index("Visual mode rules."))
        self.assertNotIn("Hard mode adds.", t)

    def test_CX_3_visual_hard_order(self):
        self.build(cfg("feature", False))
        self.switch("visual+hard")
        t = self.session_start(source="resume")
        self.assertIn("Approval is on.", t)
        self.assertLess(t.index("Approval is on."), t.index("Hard mode adds."))
        self.assertLess(t.index("Hard mode adds."), t.index("Visual mode rules."))


class TestCX4PromptLine(Base):
    def test_CX_4_status_line_per_mode(self):
        cases = [
            ("simple", False, None, "Active specguard mode is simple."),
            ("visual", False, None, "Active specguard mode is visual."),
            ("feature", False, None, "Active specguard mode is feature, approval off."),
            ("hard", False, None, "Active specguard mode is hard, approval on."),
            ("feature", True, "visual+feature", "Active specguard mode is visual+feature, approval on."),
            ("feature", False, "visual+hard", "Active specguard mode is visual+hard, approval on."),
        ]
        for default, approval, switch, expected in cases:
            with self.subTest(expected=expected):
                shutil.rmtree(self.state_home, ignore_errors=True)
                os.makedirs(self.state_home)
                shutil.rmtree(os.path.join(self.tmp, "proj"), ignore_errors=True)
                self.build(cfg(default, approval), files={"README.md": "x\n"}, git=True, commit=True)
                if switch:
                    self.switch(switch)
                self.assertEqual(self.prompt(), expected)


class TestCX5OpenRequests(Base):
    def test_CX_5_single_open_request(self):
        self.build_approval_project()
        self.open_request("docs/specs/a.md")
        self.assertEqual(
            self.prompt(),
            "Active specguard mode is feature, approval on. Open approval requests - docs/specs/a.md.",
        )

    def test_CX_5_requests_sorted_alphabetically(self):
        self.build_approval_project()
        self.open_request("docs/specs/b.md")
        self.open_request("docs/specs/a.md")
        self.assertTrue(self.prompt().endswith("Open approval requests - docs/specs/a.md, docs/specs/b.md."))

    def test_CX_5_no_request_no_sentence(self):
        self.build_approval_project()
        self.assertNotIn("Open approval requests", self.prompt())


class TestCX6RoleSessions(Base):
    def test_CX_6_role_prompt_gets_no_line(self):
        for agent_type in ["specguard:tester", "tester", "specguard:devils-advocate", "devils-advocate"]:
            with self.subTest(agent_type=agent_type):
                self.build(cfg("feature"))
                out = self.send(
                    "UserPromptSubmit", helpers.user_prompt_submit(SID, "hello", agent_type=agent_type)
                )
                self.assertIsNone(out)


class TestCX7Subagent(Base):
    def test_CX_7_general_purpose_gets_status_and_tail(self):
        self.build(cfg("feature", False))
        self.assertEqual(
            self.subagent("general-purpose"),
            "Active specguard mode is feature, approval off. " + SUBAGENT_TAIL,
        )

    def test_CX_7_open_requests_sit_between_status_and_tail(self):
        self.build_approval_project()
        self.open_request("docs/specs/a.md")
        self.assertEqual(
            self.subagent("general-purpose"),
            "Active specguard mode is feature, approval on. Open approval requests - docs/specs/a.md. "
            + SUBAGENT_TAIL,
        )


class TestCX8RoleContext(Base):
    def notes_files(self):
        return {TESTER_NOTES_PATH: "TESTER NOTES", ADVOCATE_NOTES_PATH: "ADVOCATE NOTES"}

    def test_CX_8_notes_then_blank_line_then_project_root(self):
        self.build(cfg(), files={TESTER_NOTES_PATH: "Read the spec first."})
        t = self.subagent("specguard:tester")
        self.assertTrue(t.startswith("Read the spec first.\n\nProject root: " + os.path.abspath(self.project)))

    def test_CX_8_summary_has_nine_lines_in_order(self):
        self.build(cfg(), files={TESTER_NOTES_PATH: "N"})
        t = self.subagent("specguard:tester")
        summary = t.split("\n\n", 1)[1].split("\n")
        prefixes = [
            "Project root: ",
            "Repo: ",
            "Specs dir: ",
            "Report dir: ",
            "Allowed grep roots: ",
            "Hidden: ",
            "Readable exceptions: ",
            "Hands-on tag: ",
            "Test command: ",
        ]
        self.assertEqual(len(summary), 9)
        for line, prefix in zip(summary, prefixes):
            self.assertTrue(line.startswith(prefix), (line, prefix))

    def test_CX_8_advocate_gets_advocate_notes_and_report_dir(self):
        c = cfg(reports={"tester": ".specguard/reports/tester", "advocate": ".specguard/reports/spec-review"})
        self.build(c, files=self.notes_files())
        t = self.subagent("specguard:devils-advocate")
        self.assertIn("ADVOCATE NOTES", t)
        self.assertNotIn("TESTER NOTES", t)
        self.assertIn("Report dir: .specguard/reports/spec-review", t)

    def test_CX_8_tester_gets_tester_notes_and_report_dir(self):
        c = cfg(reports={"tester": ".specguard/reports/tester", "advocate": ".specguard/reports/spec-review"})
        self.build(c, files=self.notes_files())
        t = self.subagent("specguard:tester")
        self.assertIn("TESTER NOTES", t)
        self.assertNotIn("ADVOCATE NOTES", t)
        self.assertIn("Report dir: .specguard/reports/tester", t)

    def test_CX_8_grep_roots_and_readable_exceptions(self):
        self.build(cfg(tests={"paths": ["tests"]}, tester_readable=["shared/types.ts"]))
        tester = self.subagent("specguard:tester")
        advocate = self.subagent("specguard:devils-advocate", "a2")
        self.assertIn("\nAllowed grep roots: tests, docs\n", tester)
        self.assertIn("\nAllowed grep roots: tests, docs, shared/types.ts\n", advocate)
        self.assertIn("Readable exceptions: shared/types.ts\n", tester)
        self.assertIn("Readable exceptions: shared/types.ts\n", advocate)

    def test_CX_8_empty_config_defaults(self):
        self.build({"version": 1})
        t = self.subagent("specguard:tester")
        self.assertIn("Hidden: none\n", t)
        self.assertIn("Readable exceptions: none\n", t)
        self.assertIn("Test command: none configured", t)
        self.assertIn("Hands-on tag: [hands-on]\n", t)
        self.assertIn("Repo: .\n", t)

    def test_CX_8_hidden_order_and_test_command(self):
        self.build(
            cfg(
                hidden={"segments": ["src"], "names": ["secret"], "paths": ["lib/core"]},
                stop={"run": "pytest -q"},
            )
        )
        t = self.subagent("specguard:tester")
        self.assertIn("Hidden: src, secret, lib/core\n", t)
        self.assertIn("\nTest command: pytest -q", t)

    def test_CX_8_configured_role_name_gets_role_context(self):
        self.build(cfg(roles={"tester": ["qa"]}))
        self.assertIn("Project root: ", self.subagent("qa"))

    def test_CX_8_bare_tester_gets_role_context(self):
        self.build(cfg())
        self.assertIn("Project root: ", self.subagent("tester"))

    def test_CX_8_configured_advocate_name_gets_advocate_summary(self):
        self.build(
            cfg(roles={"advocate": ["critic"]}, reports={"advocate": "rep/adv"}),
            files={ADVOCATE_NOTES_PATH: "ADV"},
        )
        t = self.subagent("critic")
        self.assertIn("ADV", t)
        self.assertIn("Report dir: rep/adv", t)

    def test_CX_8_session_start_of_role_session(self):
        self.build(cfg(), files={TESTER_NOTES_PATH: "TESTER NOTES"})
        t = self.session_start(agent_type="specguard:tester")
        self.assertTrue(t.startswith("TESTER NOTES\n\nProject root: "))
        self.assertNotIn(INTRO, t)

    def test_CX_8_role_context_has_no_intro_and_no_mode_rules(self):
        self.build(cfg("hard", True), files={TESTER_NOTES_PATH: "TN"})
        for t in (self.subagent("specguard:tester"), self.session_start(agent_type="tester")):
            self.assertNotIn("specguard-mode <set>", t)
            self.assertNotIn("Approval is", t)
            self.assertNotIn("Feature mode rules.", t)

    def test_CX_8_custom_notes_path(self):
        self.build(cfg(notes={"tester": "notes/t.md"}), files={"notes/t.md": "CUSTOM TEXT"})
        self.assertTrue(self.subagent("specguard:tester").startswith("CUSTOM TEXT\n\nProject root: "))


class TestCX9LengthCap(Base):
    def test_CX_9_huge_notes_are_replaced_by_a_pointer(self):
        self.build(cfg(), files={TESTER_NOTES_PATH: "x" * 9500})
        t = self.subagent("specguard:tester")
        self.assertNotIn("x" * 100, t)
        self.assertTrue(t.startswith("Project root: "))
        self.assertTrue(t.endswith("\nRead " + TESTER_NOTES_PATH + " first."))

    def test_CX_9_pointer_uses_configured_notes_path(self):
        self.build(cfg(notes={"tester": "notes/t.md"}), files={"notes/t.md": "y" * 9500})
        t = self.subagent("specguard:tester")
        self.assertTrue(t.endswith("Read notes/t.md first."))

    def test_CX_9_advocate_pointer_names_the_advocate_notes(self):
        self.build(
            cfg(),
            files={ADVOCATE_NOTES_PATH: "z" * 9500, TESTER_NOTES_PATH: "short"},
        )
        t = self.subagent("specguard:devils-advocate")
        self.assertNotIn("z" * 100, t)
        self.assertTrue(t.endswith("\nRead " + ADVOCATE_NOTES_PATH + " first."))

    def test_CX_9_notes_of_8000_are_included_whole(self):
        self.build(cfg(), files={TESTER_NOTES_PATH: "x" * 8000})
        t = self.subagent("specguard:tester")
        self.assertTrue(t.startswith("x" * 8000 + "\n\nProject root: "))
        self.assertNotIn("Read ", t)


class TestCX10MissingNotes(Base):
    def test_CX_10_missing_and_empty_notes_give_summary_alone(self):
        for files in (None, {TESTER_NOTES_PATH: ""}):
            with self.subTest(files=files):
                shutil.rmtree(os.path.join(self.tmp, "proj"), ignore_errors=True)
                self.build(cfg(), files=files)
                t = self.subagent("specguard:tester")
                self.assertTrue(t.startswith("Project root: "))
                self.assertNotIn("Read ", t)


class TestCX11SheetCommand(Base):
    def assert_sheet_command(self, text):
        self.assertIn("compare-sheet", text)
        self.assertNotIn("~/", text)

    def test_CX_11_default_visual_names_compare_sheet_without_home_path(self):
        self.build(cfg(default="visual"))
        self.assert_sheet_command(self.session_start())

    def test_CX_11_switched_visual_feature_names_compare_sheet_without_home_path(self):
        self.build(cfg(default="feature"))
        self.switch("visual+feature")
        self.assert_sheet_command(self.session_start(source="resume"))


if __name__ == "__main__":
    unittest.main()
