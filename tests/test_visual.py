import json
import os
import re
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

_MARKER_RE = re.compile(r"specguard-approve visual@\d+")

_VISUAL_CONFIG = {
    "version": 1,
    "modes": {"default": "visual"},
    "visual": {"ui_paths": ["app/"], "deviations_log": "docs/design-deviations.md",
               "notes": ".claude/specguard/visual-notes.md"},
}


class VisualTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-visual-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-visual-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project_dir = helpers.make_project(
            self.tmp,
            config=_VISUAL_CONFIG,
            files={"app/button.tsx": "export const Button = () => null;\n"},
            git=True,
            commit=True,
        )

    def _state_dir(self):
        key = os.path.abspath(self.project_dir).replace(os.sep, "-")
        return os.path.join(self.state_home, "specguard", key)

    def _session(self, session_id):
        path = os.path.join(self._state_dir(), "sessions", "{}.json".format(session_id))
        if not os.path.isfile(path):
            return {}
        with open(path) as f:
            return json.load(f)

    def _edit_app(self, session_id, agent_type=None):
        payload = helpers.pre_tool_use(
            session_id, "Edit",
            {"file_path": os.path.join(self.project_dir, "app/button.tsx"),
             "old_string": "null", "new_string": "'ok'"},
            **({"agent_type": agent_type} if agent_type else {}),
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

    def _text_approval(self, session_id, prompt, transcript_path):
        payload = helpers.user_prompt_submit(session_id, prompt, transcript_path=transcript_path)
        return helpers.run_hook("UserPromptSubmit", payload, self.project_dir, state_home=self.state_home)

    def _mode_switch(self, session_id, mode):
        payload = helpers.user_prompt_expansion(session_id, "specguard:mode", mode)
        return helpers.run_hook("UserPromptExpansion", payload, self.project_dir, state_home=self.state_home)

    def _ask_user_question_post(self, session_id, marker, chosen_label, options=None):
        question = "{} - your go?".format(marker)
        options = options or [{"label": "✓ Approve"}, {"label": "No"}]
        tool_input = {"questions": [{"question": question, "options": options}], "answers": {question: chosen_label}}
        payload = helpers.post_tool_use(session_id, "AskUserQuestion", tool_input, tool_input)
        return helpers.run_hook("PostToolUse", payload, self.project_dir, state_home=self.state_home)


class VisualGateTests(VisualTestCase):
    def test_deny_sets_pending(self):
        code, out, err = self._edit_app("s1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        marker = self._marker_from_reason(out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(marker, "specguard-approve visual@1")
        session = self._session("s1")
        self.assertEqual(session["visual"]["pending"], 1)
        self.assertFalse(session["visual"]["open"])

    def test_check_answer_opens_gate(self):
        code, out, _ = self._edit_app("s1")
        marker = self._marker_from_reason(out["hookSpecificOutput"]["permissionDecisionReason"])
        code2, out2, err2 = self._ask_user_question_post("s1", marker, "✓ Approve")
        self.assertEqual(code2, 0, err2)
        self.assertIn("open", out2["systemMessage"])
        session = self._session("s1")
        self.assertTrue(session["visual"]["open"])
        self.assertIsNone(session["visual"]["pending"])

        code3, out3, err3 = self._edit_app("s1")
        self.assertEqual(code3, 0, err3)
        self.assertIsNone(out3)

    def test_text_approval_right_after_marked_message_opens(self):
        code, out, _ = self._edit_app("s1")
        marker = self._marker_from_reason(out["hookSpecificOutput"]["permissionDecisionReason"])
        transcript = self._write_transcript(marker)
        code2, out2, err2 = self._text_approval("s1", "апрув", transcript)
        self.assertEqual(code2, 0, err2)
        self.assertIn("open", out2["systemMessage"])
        session = self._session("s1")
        self.assertTrue(session["visual"]["open"])

        code3, out3, err3 = self._edit_app("s1")
        self.assertIsNone(out3)

    def test_text_approval_without_marker_does_not_open(self):
        self._edit_app("s1")
        transcript = self._write_transcript("some unrelated assistant text")
        code, out, err = self._text_approval("s1", "апрув", transcript)
        self.assertEqual(code, 0, err)
        self.assertIn("no marker", out["systemMessage"])
        session = self._session("s1")
        self.assertFalse(session["visual"]["open"])

        code2, out2, err2 = self._edit_app("s1")
        self.assertEqual(out2["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_new_switch_closes_gate(self):
        code, out, _ = self._edit_app("s1")
        marker = self._marker_from_reason(out["hookSpecificOutput"]["permissionDecisionReason"])
        self._ask_user_question_post("s1", marker, "✓ Approve")
        session = self._session("s1")
        self.assertTrue(session["visual"]["open"])

        self._mode_switch("s1", "visual")
        session = self._session("s1")
        self.assertFalse(session["visual"]["open"])
        self.assertIsNone(session["visual"]["pending"])

        code2, out2, err2 = self._edit_app("s1")
        self.assertEqual(out2["hookSpecificOutput"]["permissionDecision"], "deny")
        marker2 = self._marker_from_reason(out2["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(marker2, "specguard-approve visual@2")

    def test_state_dir_write_denied_by_guard_rule_1(self):
        target = os.path.join(self._state_dir(), "sessions", "s1.json")
        payload = helpers.pre_tool_use("s1", "Write", {"file_path": target, "content": "{}"})
        code, out, err = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("state directory", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_feature_mode_untouched(self):
        project_dir = helpers.make_project(
            tempfile.mkdtemp(prefix="specguard-visual-feature-"),
            config={
                "version": 1,
                "modes": {"default": "feature", "approval_default": False},
                "visual": _VISUAL_CONFIG["visual"],
            },
            files={"app/button.tsx": "export const Button = () => null;\n"},
            git=True,
            commit=True,
        )
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        payload = helpers.pre_tool_use(
            "s1", "Edit",
            {"file_path": os.path.join(project_dir, "app/button.tsx"),
             "old_string": "null", "new_string": "'ok'"},
        )
        code, out, err = helpers.run_hook("PreToolUse", payload, project_dir, state_home=self.state_home)
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)

    def test_simple_mode_untouched(self):
        project_dir = helpers.make_project(
            tempfile.mkdtemp(prefix="specguard-visual-simple-"),
            config={"version": 1, "modes": {"default": "simple"}, "visual": _VISUAL_CONFIG["visual"]},
            files={"app/button.tsx": "export const Button = () => null;\n"},
            git=True,
            commit=True,
        )
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        payload = helpers.pre_tool_use(
            "s1", "Edit",
            {"file_path": os.path.join(project_dir, "app/button.tsx"),
             "old_string": "null", "new_string": "'ok'"},
        )
        code, out, err = helpers.run_hook("PreToolUse", payload, project_dir, state_home=self.state_home)
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)

    def test_tester_or_advocate_edit_not_this_rules_business(self):
        code, out, err = self._edit_app("s1", agent_type="specguard:tester")
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)

        # the advocate is read-only under rule 6 regardless of path; that
        # denial is not the visual rule's marker text.
        code2, out2, err2 = self._edit_app("s2", agent_type="specguard:devils-advocate")
        self.assertEqual(code2, 0, err2)
        self.assertEqual(out2["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotIn("specguard-approve visual", out2["hookSpecificOutput"]["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
