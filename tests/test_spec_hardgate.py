import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)

import helpers  # noqa: E402

ADVOCATE_REASON = "hard mode requires the devils-advocate"
APPROVAL_REASON = "spec not approved"
SPEC = "docs/specs/domain-order.md"
REPORT_DIR = ".specguard/reports/spec-review"
SESSION = "s1"
HARD = {"version": 1, "modes": {"default": "hard"}}


class HardGateBase(unittest.TestCase):
    config = HARD

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hg-proj-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="hg-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)

    def project(self, files=None, config=None, git=True, commit=True):
        cfg = self.config if config is None else config
        self.project_dir = helpers.make_project(
            self.tmp, config=cfg, files=files if files is not None else {SPEC: "v1\n"}, git=git, commit=commit
        )
        return self.project_dir

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.project_dir, check=True, capture_output=True)

    def edit(self, rel, content):
        full = os.path.join(self.project_dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def launch(self, prompt=None, subagent_type="specguard:tester", specs=(SPEC,), tool="Agent"):
        if prompt is None:
            prompt = "Write the tests for " + " and ".join(specs)
        payload = helpers.pre_tool_use(SESSION, tool, {"subagent_type": subagent_type, "prompt": prompt})
        code, out, _ = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(code, 0)
        return out

    def report(self, name, agent_type="specguard:devils-advocate", report_dir=REPORT_DIR, expect_recorded=True):
        extra = {}
        if agent_type is not None:
            extra = {"agent_type": agent_type, "agent_id": "adv-1"}
        payload = helpers.pre_tool_use(
            SESSION, "Write", {"file_path": report_dir + "/" + name, "content": "notes"}, **extra
        )
        code, out, _ = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(code, 0)
        if expect_recorded:
            self.assertIsNone(out)

    def reason(self, out):
        self.assertIsNotNone(out, "expected a refusal, got an allowed launch")
        decision = out["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        return decision["permissionDecisionReason"]

    def assertAdvocateRefusal(self, out):
        reason = self.reason(out)
        self.assertIn(ADVOCATE_REASON, reason)
        return reason

    def assertRule8Passed(self, out):
        if out is None:
            return
        reason = self.reason(out)
        self.assertNotIn(ADVOCATE_REASON, reason)
        self.assertIn(APPROVAL_REASON, reason)

    def assertNoAdvocateReason(self, out):
        if out is not None:
            self.assertNotIn(ADVOCATE_REASON, out["hookSpecificOutput"].get("permissionDecisionReason", ""))

    def log_lines(self):
        key = os.path.abspath(self.project_dir).replace("/", "-")
        path = os.path.join(self.state_home, "specguard", key, "log.jsonl")
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


class TestScope(HardGateBase):
    def test_HG_1_feature_mode_never_gets_advocate_reason(self):
        self.project(config={"version": 1, "modes": {"default": "feature"}})
        self.edit(SPEC, "v2\n")
        self.assertNoAdvocateReason(self.launch())

    def test_HG_1_general_purpose_agent_is_not_gated(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.assertNoAdvocateReason(self.launch(subagent_type="general-purpose", prompt="Read " + SPEC))

    def test_HG_1_advocate_agent_launch_is_not_gated(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.assertNoAdvocateReason(self.launch(subagent_type="specguard:devils-advocate", prompt="Review " + SPEC))

    def test_HG_1_bare_tester_name_is_gated(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.assertAdvocateRefusal(self.launch(subagent_type="tester"))

    def test_HG_1_non_agent_tool_naming_spec_is_not_gated(self):
        self.project()
        self.edit(SPEC, "v2\n")
        payload = helpers.pre_tool_use(SESSION, "Bash", {"command": "cat " + SPEC})
        _, out, _ = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertNoAdvocateReason(out)

    def test_HG_1_prompt_naming_no_spec_is_not_gated(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.assertNoAdvocateReason(self.launch(prompt="Write the tests for the order module"))


class TestUnchangedSpec(HardGateBase):
    def test_HG_2_spec_at_head_is_allowed_without_report(self):
        self.project()
        self.assertIsNone(self.launch())

    def test_HG_2_edited_spec_without_report_is_refused(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.assertAdvocateRefusal(self.launch())

    def test_HG_2_untracked_spec_without_report_is_refused(self):
        self.project(files={"README.md": "x\n"})
        self.edit(SPEC, "new\n")
        self.assertAdvocateRefusal(self.launch())


class TestReason(HardGateBase):
    def test_HG_3_reason_names_spec_and_report_dir_without_approval_marker(self):
        self.project()
        self.edit(SPEC, "v2\n")
        reason = self.assertAdvocateRefusal(self.launch())
        self.assertIn(SPEC, reason)
        self.assertIn(REPORT_DIR, reason)
        self.assertNotIn("specguard-approve", reason)

    def test_HG_3_refusal_opens_no_approval_request(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.assertAdvocateRefusal(self.launch())
        payload = helpers.user_prompt_submit(SESSION, "hello")
        code, out, _ = helpers.run_hook("UserPromptSubmit", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(code, 0)
        context = "" if out is None else out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertNotIn("Open approval requests", context)


class TestReportNames(HardGateBase):
    PASSING = [
        "domain-order-2026-09-29.md",
        "domain-order.md",
        "domain-order-2026-09-29-r2.md",
    ]
    FAILING = [
        "domain-order-total-2026-09-29.md",
        "domain-order-draft.md",
        "domain-order-2026-09-29.txt",
    ]

    def test_HG_4_advocate_report_names_that_count(self):
        for name in self.PASSING:
            with self.subTest(name=name):
                self.setUp()
                self.project()
                self.edit(SPEC, "v2\n")
                self.report(name)
                self.assertRule8Passed(self.launch())

    def test_HG_4_report_names_that_do_not_count(self):
        for name in self.FAILING:
            with self.subTest(name=name):
                self.setUp()
                self.project()
                self.edit(SPEC, "v2\n")
                self.report(name)
                self.assertAdvocateRefusal(self.launch())

    def test_HG_4_bare_devils_advocate_name_counts(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.report("domain-order-2026-09-29.md", agent_type="devils-advocate")
        self.assertRule8Passed(self.launch())

    def test_HG_4_executor_write_of_report_does_not_count(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.report("domain-order-2026-09-29.md", agent_type=None)
        self.assertAdvocateRefusal(self.launch())

    def test_HG_4_report_file_put_on_disk_without_recorded_write_does_not_count(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.edit(REPORT_DIR + "/domain-order-2026-09-29.md", "notes\n")
        self.assertAdvocateRefusal(self.launch())


class TestTiming(HardGateBase):
    def test_HG_5_report_before_any_launch_counts_after_later_edit(self):
        self.project()
        self.report("domain-order.md")
        self.edit(SPEC, "v2\n")
        self.assertRule8Passed(self.launch())

    def test_HG_5_report_recorded_before_allowed_launch_is_stale(self):
        self.project()
        self.report("domain-order.md")
        self.assertIsNone(self.launch())
        self.edit(SPEC, "v2\n")
        self.assertAdvocateRefusal(self.launch())

    def test_HG_5_report_recorded_after_allowed_launch_counts(self):
        self.project()
        self.assertIsNone(self.launch())
        self.edit(SPEC, "v2\n")
        self.report("domain-order.md")
        self.assertRule8Passed(self.launch())


class TestLastLaunchContent(HardGateBase):
    def _launch_v1_then_commit_v2(self):
        self.project()
        self.assertIsNone(self.launch())
        self.edit(SPEC, "v2\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "v2")

    def test_HG_6_content_of_last_allowed_launch_passes_though_differs_from_head(self):
        self._launch_v1_then_commit_v2()
        self.edit(SPEC, "v1\n")
        self.assertRule8Passed(self.launch())

    def test_HG_6_third_content_is_refused(self):
        self._launch_v1_then_commit_v2()
        self.edit(SPEC, "v3\n")
        self.assertAdvocateRefusal(self.launch())


class TestSeveralSpecs(HardGateBase):
    A = "docs/specs/a.md"
    B = "docs/specs/b.md"

    def test_HG_7_only_the_failing_spec_is_named(self):
        self.project(files={self.A: "a1\n", self.B: "b1\n"})
        self.edit(self.B, "b2\n")
        reason = self.assertAdvocateRefusal(self.launch(specs=(self.A, self.B)))
        self.assertIn(self.B, reason)
        self.assertNotIn(self.A, reason)

    def test_HG_7_both_failing_specs_are_named(self):
        self.project(files={self.A: "a1\n", self.B: "b1\n"})
        self.edit(self.A, "a2\n")
        self.edit(self.B, "b2\n")
        reason = self.assertAdvocateRefusal(self.launch(specs=(self.A, self.B)))
        self.assertIn(self.A, reason)
        self.assertIn(self.B, reason)

    def test_HG_7_spec_with_report_is_not_named(self):
        self.project(files={self.A: "a1\n", self.B: "b1\n"})
        self.edit(self.A, "a2\n")
        self.edit(self.B, "b2\n")
        self.report("a.md")
        reason = self.assertAdvocateRefusal(self.launch(specs=(self.A, self.B)))
        self.assertIn(self.B, reason)
        self.assertNotIn(self.A, reason)


class TestFailClosed(HardGateBase):
    def test_HG_8_no_git_repository_refuses_even_with_report(self):
        self.project(git=False, commit=False)
        self.report("domain-order.md")
        reason = self.reason(self.launch())
        self.assertIn("git", reason)

    def test_HG_8_git_repository_without_commit_refuses_even_with_report(self):
        self.project(git=True, commit=False)
        self.report("domain-order.md")
        reason = self.reason(self.launch())
        self.assertIn("git", reason)


class TestOrder(HardGateBase):
    def test_HG_9_no_report_gets_advocate_reason_not_approval(self):
        self.project()
        self.edit(SPEC, "v2\n")
        reason = self.assertAdvocateRefusal(self.launch())
        self.assertNotIn(APPROVAL_REASON, reason)

    def test_HG_9_valid_report_gets_approval_reason(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.report("domain-order.md")
        reason = self.reason(self.launch())
        self.assertIn(APPROVAL_REASON, reason)
        self.assertNotIn(ADVOCATE_REASON, reason)


class TestReportDir(HardGateBase):
    config = {"version": 1, "modes": {"default": "hard"}, "reports": {"advocate": "out/adv"}}

    def test_HG_10_report_in_configured_dir_counts(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.report("domain-order-2026-09-29.md", report_dir="out/adv")
        self.assertRule8Passed(self.launch())

    def test_HG_10_report_in_default_dir_does_not_count(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.report("domain-order-2026-09-29.md", expect_recorded=False)
        reason = self.assertAdvocateRefusal(self.launch())
        self.assertIn("out/adv", reason)


class TestRoles(HardGateBase):
    config = {
        "version": 1,
        "modes": {"default": "hard"},
        "roles": {"tester": ["qa"], "advocate": ["reviewer"]},
    }

    def test_HG_10_tester_name_from_roles_is_gated(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.assertAdvocateRefusal(self.launch(subagent_type="qa"))

    def test_HG_10_advocate_name_from_roles_counts(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.report("domain-order-2026-09-29.md", agent_type="reviewer")
        self.assertRule8Passed(self.launch(subagent_type="qa"))


class TestLog(HardGateBase):
    def _deny_lines(self):
        return [l for l in self.log_lines() if l.get("ev") == "deny" and l.get("rule") in (8, "8")]

    def test_HG_11_refusal_is_logged_with_spec(self):
        self.project()
        self.edit(SPEC, "v2\n")
        self.assertAdvocateRefusal(self.launch())
        lines = self._deny_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["spec"], SPEC)

    def test_HG_11_two_specs_are_joined_in_prompt_order(self):
        a, b = "docs/specs/a.md", "docs/specs/b.md"
        self.project(files={a: "a1\n", b: "b1\n"})
        self.edit(a, "a2\n")
        self.edit(b, "b2\n")
        self.assertAdvocateRefusal(self.launch(specs=(b, a)))
        lines = self._deny_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["spec"], b + "," + a)


if __name__ == "__main__":
    unittest.main()
