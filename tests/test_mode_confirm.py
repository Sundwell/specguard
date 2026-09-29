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


def _marker(modes_text, optout=False):
    text = "specguard-mode {}".format(modes_text)
    if optout:
        text += " no-approval"
    return text


class ModeConfirmTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-mconfirm-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-mconfirm-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project_dir = helpers.make_project(
            self.tmp,
            config={"version": 1, "modes": {"default": "simple", "approval_default": True}},
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

    def _log_lines(self):
        path = os.path.join(self._state_dir(), "log.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def _write_transcript(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        path = os.path.join(self.tmp, "transcript-{}.jsonl".format(len(os.listdir(self.tmp))))
        with open(path, "w") as f:
            for text in texts:
                rec = {"message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
                f.write(json.dumps(rec) + "\n")
        return path

    def _ask(self, session_id, question, options, answers_after=None, **extra):
        tool_input = {"questions": [{"question": question, "options": options}]}
        pre_payload = helpers.pre_tool_use(session_id, "AskUserQuestion", tool_input, **extra)
        _code, pre_out, _err = helpers.run_hook(
            "PreToolUse", pre_payload, self.project_dir, state_home=self.state_home
        )
        post_out = None
        if answers_after is not None:
            tool_input_post = dict(tool_input)
            tool_input_post["answers"] = {question: answers_after}
            post_payload = helpers.post_tool_use(
                session_id, "AskUserQuestion", tool_input_post, tool_input_post, **extra
            )
            _c2, post_out, _e2 = helpers.run_hook(
                "PostToolUse", post_payload, self.project_dir, state_home=self.state_home
            )
        return pre_out, post_out

    def _text_confirm(self, session_id, prompt, transcript_path, **extra):
        payload = helpers.user_prompt_submit(session_id, prompt, transcript_path=transcript_path, **extra)
        return helpers.run_hook("UserPromptSubmit", payload, self.project_dir, state_home=self.state_home)


class StructureTests(ModeConfirmTestCase):
    def test_no_check_option_denied(self):
        question = "Switch? {}".format(_marker("simple"))
        pre, _post = self._ask("s1", question, [{"label": "Switch"}, {"label": "Stay"}])
        self.assertEqual(pre["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_two_check_options_denied(self):
        question = "Switch? {}".format(_marker("simple"))
        pre, _post = self._ask("s1", question, [{"label": "✓ Switch"}, {"label": "✓ Also"}])
        self.assertEqual(pre["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_single_option_denied(self):
        question = "Switch? {}".format(_marker("simple"))
        pre, _post = self._ask("s1", question, [{"label": "✓ Switch"}])
        self.assertEqual(pre["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_prefilled_answers_denied(self):
        question = "Switch? {}".format(_marker("simple"))
        options = [{"label": "✓ Switch"}, {"label": "Stay"}]
        tool_input = {
            "questions": [{"question": question, "options": options}],
            "answers": {question: options[0]["label"]},
        }
        payload = helpers.pre_tool_use("s1", "AskUserQuestion", tool_input)
        code, out, err = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_valid_structure_allowed(self):
        question = "Switch? {}".format(_marker("simple"))
        pre, _post = self._ask("s1", question, [{"label": "✓ Switch"}, {"label": "Stay"}])
        self.assertIsNone(pre)


class MixedMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-mconfirm-mix-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-mconfirm-mix-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)

    def _ask(self, session_id, question, options, project_dir):
        tool_input = {"questions": [{"question": question, "options": options}]}
        pre_payload = helpers.pre_tool_use(session_id, "AskUserQuestion", tool_input)
        _code, pre_out, _err = helpers.run_hook(
            "PreToolUse", pre_payload, project_dir, state_home=self.state_home
        )
        return pre_out

    def test_mode_marker_mixed_with_spec_marker_denied(self):
        project_dir = helpers.make_project(
            self.tmp,
            config={"version": 1, "modes": {"default": "feature", "approval_default": True}},
            files={"docs/specs/x.md": "rule one\n"},
            git=True,
            commit=True,
        )
        with open(os.path.join(project_dir, "docs/specs/x.md"), "w") as f:
            f.write("edited\n")
        payload = helpers.pre_tool_use(
            "s1", "Agent",
            {"subagent_type": "specguard:tester", "prompt": "Run the tester on docs/specs/x.md"},
        )
        _code, out, _err = helpers.run_hook("PreToolUse", payload, project_dir, state_home=self.state_home)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        spec_marker = re.search(r"specguard-approve \S+@[0-9a-f]{7}", reason).group(0)

        question = "{} and {} - go?".format(spec_marker, _marker("simple"))
        pre = self._ask("s1", question, [{"label": "✓ Both"}, {"label": "No"}], project_dir)
        self.assertEqual(pre["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("mix", pre["hookSpecificOutput"]["permissionDecisionReason"])

    def test_mode_marker_mixed_with_visual_marker_denied(self):
        project_dir = helpers.make_project(
            self.tmp,
            config={"version": 1, "modes": {"default": "visual"}, "visual": {"ui_paths": ["app/"]}},
            files={"app/button.tsx": "export const Button = () => null;\n"},
            git=True,
            commit=True,
        )
        edit_payload = helpers.pre_tool_use(
            "s1", "Edit",
            {"file_path": os.path.join(project_dir, "app/button.tsx"),
             "old_string": "null", "new_string": "'ok'"},
        )
        _code, out, _err = helpers.run_hook("PreToolUse", edit_payload, project_dir, state_home=self.state_home)
        visual_marker = re.search(r"specguard-approve visual@\d+", out["hookSpecificOutput"]["permissionDecisionReason"]).group(0)

        question = "{} and {} - go?".format(visual_marker, _marker("simple"))
        pre = self._ask("s1", question, [{"label": "✓ Both"}, {"label": "No"}], project_dir)
        self.assertEqual(pre["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("mix", pre["hookSpecificOutput"]["permissionDecisionReason"])


class SwitchViaAskTests(ModeConfirmTestCase):
    def test_check_answer_switches_and_logs_via_ask(self):
        question = "Switch to hard? {}".format(_marker("hard"))
        _pre, post = self._ask(
            "s1", question, [{"label": "✓ Switch"}, {"label": "Stay"}], answers_after="✓ Switch"
        )
        self.assertIn("confirmed by you", post["systemMessage"])
        session = self._session("s1")
        self.assertEqual(session["modes"], ["hard"])
        self.assertTrue(session["approval"])
        matches = [
            r for r in self._log_lines() if r.get("ev") == "mode-switch" and r.get("via") == "ask"
        ]
        self.assertTrue(matches)
        self.assertEqual(matches[-1]["modes"], ["hard"])

    def test_other_answer_leaves_mode_unchanged(self):
        question = "Switch to hard? {}".format(_marker("hard"))
        _pre, post = self._ask(
            "s1", question, [{"label": "✓ Switch"}, {"label": "Stay"}], answers_after="Stay"
        )
        self.assertIn("mode unchanged", post["systemMessage"])
        session = self._session("s1")
        self.assertNotIn("modes", session)


class TextConfirmTests(ModeConfirmTestCase):
    def test_confirm_words_switch_after_marked_message(self):
        for word in ("да", "+", "yes", "так", "го"):
            with self.subTest(word=word):
                sid = "text-" + word.encode("utf-8").hex()
                transcript = self._write_transcript("Хочешь простой режим? {}".format(_marker("simple")))
                code, out, err = self._text_confirm(sid, word, transcript)
                self.assertEqual(code, 0, err)
                self.assertIn("confirmed by you", out["systemMessage"])
                session = self._session(sid)
                self.assertEqual(session["modes"], ["simple"])
                matches = [
                    r for r in self._log_lines()
                    if r.get("ev") == "mode-switch" and r.get("sid") == sid and r.get("via") == "text"
                ]
                self.assertTrue(matches)

    def test_confirm_words_after_unmarked_message_do_nothing(self):
        for word in ("да", "+", "yes", "так", "го"):
            with self.subTest(word=word):
                sid = "unmarked-" + word.encode("utf-8").hex()
                transcript = self._write_transcript("some unrelated text")
                code, out, err = self._text_confirm(sid, word, transcript)
                self.assertEqual(code, 0, err)
                if out is not None:
                    self.assertNotIn("confirmed by you", out.get("systemMessage", ""))
                session = self._session(sid)
                self.assertNotIn("modes", session)

    def test_longer_message_does_not_count(self):
        transcript = self._write_transcript("Хочешь простой режим? {}".format(_marker("simple")))
        code, out, err = self._text_confirm("s1", "да, но сначала покажи", transcript)
        self.assertEqual(code, 0, err)
        if out is not None:
            self.assertNotIn("confirmed by you", out.get("systemMessage", ""))
        session = self._session("s1")
        self.assertNotIn("modes", session)

    def test_marker_earlier_not_in_last_message_does_nothing(self):
        transcript = self._write_transcript([
            "Switch? {}".format(_marker("simple")),
            "actually, forget it",
        ])
        code, out, err = self._text_confirm("s1", "да", transcript)
        self.assertEqual(code, 0, err)
        if out is not None:
            self.assertNotIn("confirmed by you", out.get("systemMessage", ""))
        session = self._session("s1")
        self.assertNotIn("modes", session)


class HardModeConfirmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-mconfirm-hard-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-mconfirm-hard-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project_dir = helpers.make_project(
            self.tmp, config={"version": 1, "modes": {"default": "hard"}}
        )

    def _session(self, session_id):
        key = os.path.abspath(self.project_dir).replace(os.sep, "-")
        path = os.path.join(self.state_home, "specguard", key, "sessions", "{}.json".format(session_id))
        if not os.path.isfile(path):
            return {}
        with open(path) as f:
            return json.load(f)

    def _ask(self, session_id, question, options, answers_after):
        tool_input = {"questions": [{"question": question, "options": options}]}
        pre_payload = helpers.pre_tool_use(session_id, "AskUserQuestion", tool_input)
        helpers.run_hook("PreToolUse", pre_payload, self.project_dir, state_home=self.state_home)
        tool_input_post = dict(tool_input)
        tool_input_post["answers"] = {question: answers_after}
        post_payload = helpers.post_tool_use(session_id, "AskUserQuestion", tool_input_post, tool_input_post)
        _code, post_out, _err = helpers.run_hook(
            "PostToolUse", post_payload, self.project_dir, state_home=self.state_home
        )
        return post_out

    def test_leaving_hard_by_confirm_works(self):
        question = "Switch to simple? {}".format(_marker("simple"))
        post = self._ask("s1", question, [{"label": "✓ Switch"}, {"label": "Stay"}], "✓ Switch")
        self.assertIn("confirmed by you", post["systemMessage"])
        session = self._session("s1")
        self.assertEqual(session["modes"], ["simple"])

    def test_feature_no_approval_confirm_leaves_hard_with_approval_off(self):
        question = "Switch to feature, no approval? {}".format(_marker("feature", optout=True))
        post = self._ask("s1", question, [{"label": "✓ Switch"}, {"label": "Stay"}], "✓ Switch")
        self.assertIn("confirmed by you", post["systemMessage"])
        session = self._session("s1")
        self.assertEqual(session["modes"], ["feature"])
        self.assertFalse(session["approval"])

    def test_no_approval_into_hard_via_confirm_refused(self):
        question = "Switch to hard, no approval? {}".format(_marker("hard", optout=True))
        post = self._ask("s1", question, [{"label": "✓ Switch"}, {"label": "Stay"}], "✓ Switch")
        self.assertIn("refused", post["systemMessage"])
        session = self._session("s1")
        self.assertEqual(session["modes"], ["hard"])
        self.assertTrue(session["approval"])


class MiscConfirmTests(ModeConfirmTestCase):
    def test_role_session_ignored_for_text_confirm(self):
        transcript = self._write_transcript("Switch? {}".format(_marker("simple")))
        code, out, err = self._text_confirm("s1", "да", transcript, agent_type="specguard:tester")
        self.assertEqual(code, 0, err)
        if out is not None:
            self.assertNotIn("confirmed by you", out.get("systemMessage", ""))
        session = self._session("s1")
        self.assertNotIn("modes", session)

    def test_invalid_set_in_marker_records_nothing(self):
        transcript = self._write_transcript("Switch? {}".format(_marker("simple+hard")))
        code, out, err = self._text_confirm("s1", "да", transcript)
        self.assertEqual(code, 0, err)
        self.assertIn("not a valid combination", out["systemMessage"])
        self.assertIn("accepted are", out["systemMessage"])
        session = self._session("s1")
        self.assertNotIn("modes", session)

    def test_session_start_context_carries_rule(self):
        payload = helpers.session_start("s1")
        code, out, err = helpers.run_hook("SessionStart", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(code, 0, err)
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("specguard-mode <set>", text)


if __name__ == "__main__":
    unittest.main()
