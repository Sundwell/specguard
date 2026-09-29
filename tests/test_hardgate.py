import json
import os
import re
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

_MARKER_RE = re.compile(r"specguard-approve \S+@[0-9a-f]{7}")
_SPEC_REL = "docs/specs/domain-order.md"
_REPORT_DIR = ".specguard/reports/spec-review"


class HardgateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-hardgate-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-hardgate-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project_dir = helpers.make_project(
            self.tmp,
            config={"version": 1, "modes": {"default": "hard"}},
            files={_SPEC_REL: "rule one\n"},
            git=True,
            commit=True,
        )

    # -- state helpers -------------------------------------------------

    def _state_dir(self):
        key = os.path.abspath(self.project_dir).replace(os.sep, "-")
        return os.path.join(self.state_home, "specguard", key)

    def _session(self, session_id):
        path = os.path.join(self._state_dir(), "sessions", "{}.json".format(session_id))
        if not os.path.isfile(path):
            return {}
        with open(path) as f:
            return json.load(f)

    def _json(self, name):
        path = os.path.join(self._state_dir(), name)
        if not os.path.isfile(path):
            return {}
        with open(path) as f:
            return json.load(f)

    def _write_spec(self, text=_SPEC_REL, content="rule one edited\n"):
        with open(os.path.join(self.project_dir, text), "w") as f:
            f.write(content)

    # -- driving the hook -----------------------------------------------

    def _launch_tester(self, session_id, spec=_SPEC_REL):
        payload = helpers.pre_tool_use(
            session_id,
            "Agent",
            {"subagent_type": "specguard:tester", "prompt": "Run the tester on {}".format(spec)},
        )
        return helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)

    def _advocate_write(self, session_id, report_name, agent_id="adv-1"):
        rel_path = os.path.join(_REPORT_DIR, report_name)
        payload = helpers.pre_tool_use(
            session_id, "Write", {"file_path": rel_path, "content": "advocate notes"},
            agent_type="specguard:devils-advocate", agent_id=agent_id,
        )
        return helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)

    def _executor_write(self, session_id, report_name):
        rel_path = os.path.join(_REPORT_DIR, report_name)
        payload = helpers.pre_tool_use(
            session_id, "Write", {"file_path": rel_path, "content": "not really an advocate"},
        )
        return helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)

    def _marker_from_reason(self, reason):
        m = _MARKER_RE.search(reason)
        self.assertIsNotNone(m, reason)
        return m.group(0)

    def _write_transcript(self, text):
        path = os.path.join(self.tmp, "transcript-{}.jsonl".format(len(os.listdir(self.tmp))))
        with open(path, "w") as f:
            f.write(json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}) + "\n")
        return path

    def _approve_by_text(self, session_id, marker):
        transcript = self._write_transcript(marker)
        payload = helpers.user_prompt_submit(session_id, "апрув", transcript_path=transcript)
        return helpers.run_hook("UserPromptSubmit", payload, self.project_dir, state_home=self.state_home)

    def _pass_rule9(self, session_id, spec=_SPEC_REL):
        """Deny at rule 9, then approve by text so the next launch is allowed."""
        code, out, err = self._launch_tester(session_id, spec=spec)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("not approved", reason)
        marker = self._marker_from_reason(reason)
        code, out, err = self._approve_by_text(session_id, marker)
        self.assertEqual(code, 0, err)
        self.assertIn("approved", out["systemMessage"])


class SuccessPathTests(HardgateTestCase):
    def test_advocate_report_spec_fix_rule9_deny_approval_launch(self):
        code, out, err = self._advocate_write("adv1", "domain-order-2026-09-23.md")
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)

        self._write_spec()

        # rule 8 passes (report on file), rule 9 denies and opens a request,
        # then a text approval records it.
        self._pass_rule9("s1")

        code, out, err = self._launch_tester("s1")
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)
        launches = self._json("launches.json")
        self.assertIn(_SPEC_REL, launches)

    def test_relaunch_on_unchanged_spec_passes(self):
        self._advocate_write("adv1", "domain-order-2026-09-23.md")
        self._write_spec()
        self._pass_rule9("s1")
        code, out, err = self._launch_tester("s1")
        self.assertIsNone(out)

        code, out, err = self._launch_tester("s1")
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)

    def test_edit_needs_new_report(self):
        self._advocate_write("adv1", "domain-order-2026-09-23.md")
        self._write_spec()
        self._pass_rule9("s1")
        code, out, err = self._launch_tester("s1")
        self.assertIsNone(out)

        # edit again - the old report predates the last allowed launch, so it
        # no longer covers the spec.
        self._write_spec(content="rule one edited again\n")
        code, out, err = self._launch_tester("s1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("devils-advocate", out["hookSpecificOutput"]["permissionDecisionReason"])


class ReportAttributionTests(HardgateTestCase):
    def test_executor_written_report_does_not_count(self):
        code, out, err = self._executor_write("s1", "domain-order-2026-09-23.md")
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)
        self.assertEqual(self._json("advocate-reports.json"), {})

        self._write_spec()
        code, out, err = self._launch_tester("s1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("devils-advocate", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_mismatched_stem_does_not_count(self):
        code, out, err = self._advocate_write("adv1", "domain-order-total-2026-09-23.md")
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)

        self._write_spec()
        code, out, err = self._launch_tester("s1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("devils-advocate", out["hookSpecificOutput"]["permissionDecisionReason"])


class GitFailureTests(HardgateTestCase):
    def test_git_failure_denies(self):
        project_dir = helpers.make_project(
            tempfile.mkdtemp(prefix="specguard-hardgate-nogit-"),
            config={"version": 1, "modes": {"default": "hard"}},
            files={_SPEC_REL: "rule one\n"},
            git=False,
        )
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        payload = helpers.pre_tool_use(
            "s1", "Agent",
            {"subagent_type": "specguard:tester", "prompt": "Run the tester on {}".format(_SPEC_REL)},
        )
        code, out, err = helpers.run_hook("PreToolUse", payload, project_dir, state_home=self.state_home)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("git", out["hookSpecificOutput"]["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
