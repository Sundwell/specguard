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


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ApprovalTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-approval-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-approval-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project_dir = helpers.make_project(
            self.tmp,
            config={"version": 1, "modes": {"default": "feature", "approval_default": True}},
            files={"docs/specs/x.md": "rule one\n"},
            git=True,
            commit=True,
        )

    # -- helpers -----------------------------------------------------

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

    def _launch_tester(self, session_id, spec="docs/specs/x.md", agent_id=None, extra=None):
        payload = helpers.pre_tool_use(
            session_id,
            "Agent",
            {"subagent_type": "specguard:tester", "prompt": "Run the tester on {}".format(spec)},
            **(extra or {}),
        )
        if agent_id:
            payload["agent_id"] = agent_id
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

    def _ask_user_question(self, session_id, marker, chosen_label, options=None, prefilled=False, transcript_path=None):
        question = "{} - approve?".format(marker)
        options = options or [{"label": "✓ Approve"}, {"label": "No"}]
        tool_input = {"questions": [{"question": question, "options": options}]}
        if prefilled:
            tool_input["answers"] = {question: options[0]["label"]}
        pre_payload = helpers.pre_tool_use(session_id, "AskUserQuestion", tool_input)
        if transcript_path:
            pre_payload["transcript_path"] = transcript_path
        _code, pre_out, _err = helpers.run_hook(
            "PreToolUse", pre_payload, self.project_dir, state_home=self.state_home
        )

        tool_input_post = dict(tool_input)
        tool_input_post["answers"] = {question: chosen_label}
        post_payload = helpers.post_tool_use(session_id, "AskUserQuestion", tool_input_post, tool_input_post)
        if transcript_path:
            post_payload["transcript_path"] = transcript_path
        _code2, post_out, _err2 = helpers.run_hook(
            "PostToolUse", post_payload, self.project_dir, state_home=self.state_home
        )
        return pre_out, post_out

    def _text_approval(self, session_id, prompt, transcript_path):
        payload = helpers.user_prompt_submit(session_id, prompt, transcript_path=transcript_path)
        return helpers.run_hook("UserPromptSubmit", payload, self.project_dir, state_home=self.state_home)


class SpecGateTests(ApprovalTestCase):
    def test_committed_spec_passes(self):
        code, out, err = self._launch_tester("s1")
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)
        with open(os.path.join(self._state_dir(), "launches.json")) as f:
            launches = json.load(f)
        self.assertIn("docs/specs/x.md", launches)

    def test_edited_spec_denied_with_request(self):
        with open(os.path.join(self.project_dir, "docs/specs/x.md"), "w") as f:
            f.write("rule one edited\n")
        code, out, err = self._launch_tester("s1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self._marker_from_reason(reason)
        session = self._session("s1")
        self.assertIn("docs/specs/x.md", session["requests"])

    def test_untracked_spec_not_approved(self):
        with open(os.path.join(self.project_dir, "docs/specs/y.md"), "w") as f:
            f.write("untracked\n")
        code, out, err = self._launch_tester("s1", spec="docs/specs/y.md")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_assume_unchanged_does_not_hide_edit(self):
        _git("update-index", "--assume-unchanged", "docs/specs/x.md", cwd=self.project_dir)
        with open(os.path.join(self.project_dir, "docs/specs/x.md"), "w") as f:
            f.write("rule one edited\n")
        code, out, err = self._launch_tester("s1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_skip_worktree_does_not_hide_edit(self):
        _git("update-index", "--skip-worktree", "docs/specs/x.md", cwd=self.project_dir)
        with open(os.path.join(self.project_dir, "docs/specs/x.md"), "w") as f:
            f.write("rule one edited\n")
        code, out, err = self._launch_tester("s1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_no_spec_named_denied(self):
        payload = helpers.pre_tool_use(
            "s1", "Agent", {"subagent_type": "specguard:tester", "prompt": "Run the tests please"}
        )
        code, out, err = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("name the spec", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_nested_subagent_request_lands_in_main_session(self):
        # the subagent's own PreToolUse carries the parent's session_id
        # (Phase 0 C2); record it as a subagent of the main session first.
        subagent_payload = helpers.subagent_start("main1", "agent-1", "some-executor-subagent")
        helpers.run_hook("SubagentStart", subagent_payload, self.project_dir, state_home=self.state_home)
        with open(os.path.join(self.project_dir, "docs/specs/x.md"), "w") as f:
            f.write("rule one edited\n")
        code, out, err = self._launch_tester("main1", agent_id="agent-1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        session = self._session("main1")
        self.assertIn("docs/specs/x.md", session["requests"])

    def test_two_requests_one_approval_covers_only_matching_marker(self):
        with open(os.path.join(self.project_dir, "docs/specs/x.md"), "w") as f:
            f.write("rule one edited\n")
        with open(os.path.join(self.project_dir, "docs/specs/y.md"), "w") as f:
            f.write("second spec\n")
        code1, out1, _ = self._launch_tester("s1", spec="docs/specs/x.md")
        code2, out2, _ = self._launch_tester("s1", spec="docs/specs/y.md")
        session = self._session("s1")
        self.assertEqual(set(session["requests"].keys()), {"docs/specs/x.md", "docs/specs/y.md"})

        marker_x = self._marker_from_reason(out1["hookSpecificOutput"]["permissionDecisionReason"])
        transcript = self._write_transcript(marker_x)
        code, out, err = self._text_approval("s1", "апрув", transcript)
        self.assertEqual(code, 0, err)
        session = self._session("s1")
        self.assertNotIn("docs/specs/x.md", session["requests"])
        self.assertIn("docs/specs/y.md", session["requests"])


class AskUserQuestionTests(ApprovalTestCase):
    def _open_request(self, session_id="s1", spec="docs/specs/x.md"):
        with open(os.path.join(self.project_dir, spec), "w") as f:
            f.write("edited\n")
        code, out, err = self._launch_tester(session_id, spec=spec)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        return self._marker_from_reason(reason)

    def test_check_answer_records_approval(self):
        marker = self._open_request()
        pre, post = self._ask_user_question("s1", marker, "✓ Approve")
        self.assertIsNone(pre)
        self.assertIn("approved", post["systemMessage"])
        session = self._session("s1")
        self.assertNotIn("docs/specs/x.md", session["requests"])
        self.assertEqual(session["last_approval"][0][0], "docs/specs/x.md")

    def test_other_answer_closes_request(self):
        marker = self._open_request()
        pre, post = self._ask_user_question("s1", marker, "No")
        self.assertIsNone(pre)
        self.assertIn("closed", post["systemMessage"])
        session = self._session("s1")
        self.assertNotIn("docs/specs/x.md", session["requests"])
        self.assertEqual(session.get("last_approval", []), [])

    def test_no_check_option_denied(self):
        marker = self._open_request()
        question = "{} - approve?".format(marker)
        payload = helpers.pre_tool_use(
            "s1", "AskUserQuestion",
            {"questions": [{"question": question, "options": [{"label": "Yes"}, {"label": "No"}]}]},
        )
        code, out, err = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_two_check_options_denied(self):
        marker = self._open_request()
        question = "{} - approve?".format(marker)
        payload = helpers.pre_tool_use(
            "s1", "AskUserQuestion",
            {"questions": [{"question": question, "options": [{"label": "✓ A"}, {"label": "✓ B"}]}]},
        )
        code, out, err = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_prefilled_answers_denied(self):
        marker = self._open_request()
        pre_out, _post_out = self._ask_user_question("s1", marker, "✓ Approve", prefilled=True)
        self.assertEqual(pre_out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_missing_marker_records_nothing(self):
        self._open_request()
        payload_pre = helpers.pre_tool_use(
            "s1", "AskUserQuestion",
            {"questions": [{"question": "unrelated question", "options": [{"label": "✓ A"}, {"label": "No"}]}]},
        )
        code, out, err = helpers.run_hook("PreToolUse", payload_pre, self.project_dir, state_home=self.state_home)
        self.assertIsNone(out)
        payload_post = helpers.post_tool_use(
            "s1", "AskUserQuestion",
            {"questions": [{"question": "unrelated question", "options": [{"label": "✓ A"}, {"label": "No"}]}],
             "answers": {"unrelated question": "✓ A"}},
            {},
        )
        code, out, err = helpers.run_hook("PostToolUse", payload_post, self.project_dir, state_home=self.state_home)
        self.assertIsNone(out)
        session = self._session("s1")
        self.assertIn("docs/specs/x.md", session["requests"])

    def test_wrong_hash_records_nothing(self):
        marker = self._open_request()
        wrong_marker = marker[:-1] + ("0" if marker[-1] != "0" else "1")
        question = "{} - approve?".format(wrong_marker)
        payload = helpers.post_tool_use(
            "s1", "AskUserQuestion",
            {"questions": [{"question": question, "options": [{"label": "✓ A"}, {"label": "No"}]}],
             "answers": {question: "✓ A"}},
            {},
        )
        code, out, err = helpers.run_hook("PostToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertIsNone(out)
        session = self._session("s1")
        self.assertIn("docs/specs/x.md", session["requests"])

    def test_spec_edited_between_request_and_answer_stays_open(self):
        marker = self._open_request()
        with open(os.path.join(self.project_dir, "docs/specs/x.md"), "w") as f:
            f.write("edited again\n")
        pre, post = self._ask_user_question("s1", marker, "✓ Approve")
        self.assertIsNone(post)
        session = self._session("s1")
        self.assertIn("docs/specs/x.md", session["requests"])


class TextApprovalTests(ApprovalTestCase):
    def _open_request(self, session_id="s1", spec="docs/specs/x.md"):
        with open(os.path.join(self.project_dir, spec), "w") as f:
            f.write("edited\n")
        code, out, err = self._launch_tester(session_id, spec=spec)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        return self._marker_from_reason(reason)

    def test_approves_only_after_marked_last_message(self):
        marker = self._open_request()
        transcript = self._write_transcript(marker)
        code, out, err = self._text_approval("s1", "апрув", transcript)
        self.assertEqual(code, 0, err)
        self.assertIn("approved", out["systemMessage"])
        session = self._session("s1")
        self.assertNotIn("docs/specs/x.md", session["requests"])

    def test_lone_da_records_nothing(self):
        self._open_request()
        transcript = self._write_transcript("some unrelated text without a marker")
        code, out, err = self._text_approval("s1", "да", transcript)
        self.assertEqual(code, 0, err)
        if out is not None:
            self.assertNotIn("systemMessage", out)
        session = self._session("s1")
        self.assertIn("docs/specs/x.md", session["requests"])

    def test_go_after_unrelated_message_records_nothing(self):
        self._open_request()
        transcript = self._write_transcript("Делать?")
        code, out, err = self._text_approval("s1", "го", transcript)
        self.assertEqual(code, 0, err)
        # "го" stays silent even when nothing is recorded (only the routine
        # one-line status context may still be present)
        if out is not None:
            self.assertNotIn("systemMessage", out)
        session = self._session("s1")
        self.assertIn("docs/specs/x.md", session["requests"])

    def test_apruv_with_no_marker_reports_not_recorded(self):
        self._open_request()
        transcript = self._write_transcript("some unrelated text")
        code, out, err = self._text_approval("s1", "апрув", transcript)
        self.assertEqual(code, 0, err)
        self.assertIn("no marker", out["systemMessage"])

    def test_apruv_with_no_open_request_reports_nothing_to_approve(self):
        transcript = self._write_transcript("nothing relevant")
        code, out, err = self._text_approval("s1", "апрув", transcript)
        self.assertEqual(code, 0, err)
        self.assertIn("nothing to approve", out["systemMessage"])


class RevokeTests(ApprovalTestCase):
    def test_revoke_removes_last_approval(self):
        with open(os.path.join(self.project_dir, "docs/specs/x.md"), "w") as f:
            f.write("edited\n")
        code, out, err = self._launch_tester("s1")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        marker = self._marker_from_reason(reason)
        transcript = self._write_transcript(marker)
        self._text_approval("s1", "апрув", transcript)

        code, out, err = self._text_approval("s1", "отзываю апрув", transcript)
        self.assertEqual(code, 0, err)
        self.assertIn("revoked", out["systemMessage"])

        with open(os.path.join(self._state_dir(), "approved-specs.json")) as f:
            approved = json.load(f)
        self.assertNotIn("docs/specs/x.md", approved)

        # a relaunch on the still-unchanged spec must be denied again
        code2, out2, err2 = self._launch_tester("s1")
        self.assertEqual(out2["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_revoke_with_nothing_to_revoke(self):
        transcript = self._write_transcript("nothing")
        code, out, err = self._text_approval("s1", "отзываю апрув", transcript)
        self.assertEqual(code, 0, err)
        self.assertIn("nothing to revoke", out["systemMessage"])


class SendMessageAndScheduleTests(ApprovalTestCase):
    def test_send_message_to_tester_denied_in_gated_mode(self):
        helpers.run_hook(
            "SubagentStart",
            helpers.subagent_start("s1", "tester-1", "specguard:tester"),
            self.project_dir,
            state_home=self.state_home,
        )
        payload = helpers.pre_tool_use("s1", "SendMessage", {"to": "tester-1", "message": "please continue"})
        code, out, err = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_send_message_to_tester_allowed_in_simple(self):
        project_dir = helpers.make_project(
            tempfile.mkdtemp(prefix="specguard-simple-"),
            config={"version": 1, "modes": {"default": "simple"}},
            git=True,
            commit=True,
        )
        self.addCleanup(shutil.rmtree, project_dir, ignore_errors=True)
        helpers.run_hook(
            "SubagentStart",
            helpers.subagent_start("s1", "tester-1", "specguard:tester"),
            project_dir,
            state_home=self.state_home,
        )
        payload = helpers.pre_tool_use("s1", "SendMessage", {"to": "tester-1", "message": "please continue"})
        code, out, err = helpers.run_hook("PreToolUse", payload, project_dir, state_home=self.state_home)
        self.assertIsNone(out)

    def test_cron_create_carrying_approval_word_denied(self):
        payload = helpers.pre_tool_use("s1", "CronCreate", {"prompt": "апрув"})
        code, out, err = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_schedule_wakeup_carrying_mode_phrase_denied(self):
        payload = helpers.pre_tool_use("s1", "ScheduleWakeup", {"prompt": "го хардмод"})
        code, out, err = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_ordinary_scheduled_text_allowed(self):
        payload = helpers.pre_tool_use("s1", "CronCreate", {"prompt": "run the nightly build"})
        code, out, err = helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)
        self.assertIsNone(out)


class SpecReferenceFormTests(unittest.TestCase):
    """Rule 9's spec-reference extraction on a parfume-shaped project - the
    outer folder is the project root, the repo is the inner ``sample-shops``
    folder, so a natural reference in a launch prompt carries the repo
    prefix. Fixed after a live-run bug (see runs/1c.md, "Fix after live
    run")."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-specref-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-specref-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project_dir = helpers.make_project(
            self.tmp,
            config={"version": 1, "repo": "sample-shops", "modes": {"default": "feature"}},
            files={"sample-shops/docs/specs/domain-phone.md": "rule one\n"},
            git=False,
        )
        # the repo is the inner sample-shops folder; commit there, not at
        # the outer project root, matching the real parfume-stuff layout.
        self.repo_dir = os.path.join(self.project_dir, "sample-shops")
        _git("init", "-q", cwd=self.repo_dir)
        _git("config", "user.email", "t@example.com", cwd=self.repo_dir)
        _git("config", "user.name", "specguard tests", cwd=self.repo_dir)
        _git("add", "-A", cwd=self.repo_dir)
        _git("commit", "-q", "-m", "initial", cwd=self.repo_dir)

    def _run(self, prompt, session_id="s1", cwd=None):
        payload = helpers.pre_tool_use(
            session_id,
            "Agent",
            {"subagent_type": "specguard:tester", "prompt": prompt},
        )
        if cwd:
            payload["cwd"] = cwd
        return helpers.run_hook("PreToolUse", payload, self.project_dir, state_home=self.state_home)

    def _assert_passes(self, prompt, session_id, cwd=None):
        code, out, err = self._run(prompt, session_id=session_id, cwd=cwd)
        self.assertEqual(code, 0, err)
        self.assertIsNone(out, "expected no deny for prompt {!r}, got {}".format(prompt, out))

    def test_repo_relative_reference(self):
        self._assert_passes(
            "Run specguard:tester on docs/specs/domain-phone.md", "repo-rel", cwd=self.repo_dir
        )

    def test_project_relative_reference_with_repo_prefix(self):
        self._assert_passes(
            "Run specguard:tester on sample-shops/docs/specs/domain-phone.md",
            "project-rel",
            cwd=self.project_dir,
        )

    def test_absolute_reference(self):
        abs_path = os.path.join(self.repo_dir, "docs", "specs", "domain-phone.md")
        self._assert_passes(
            "Run specguard:tester on {}".format(abs_path), "abs-ref", cwd=self.project_dir
        )

    def test_dot_slash_prefixed_reference(self):
        self._assert_passes(
            "Run specguard:tester on ./docs/specs/domain-phone.md", "dot-slash", cwd=self.repo_dir
        )

    def test_quoted_reference(self):
        self._assert_passes(
            'Run specguard:tester on "sample-shops/docs/specs/domain-phone.md"',
            "quoted",
            cwd=self.project_dir,
        )

    def test_backticked_reference(self):
        self._assert_passes(
            "Run specguard:tester on `sample-shops/docs/specs/domain-phone.md`",
            "backticked",
            cwd=self.project_dir,
        )

    def test_trailing_comma_period_paren_colon(self):
        for suffix, label in ((",", "comma"), (".", "period"), (")", "paren"), (":", "colon")):
            with self.subTest(suffix=suffix):
                prompt = "Run specguard:tester on sample-shops/docs/specs/domain-phone.md{} now".format(
                    suffix
                )
                self._assert_passes(prompt, "trail-" + label, cwd=self.project_dir)

    def test_bare_name_reference(self):
        self._assert_passes(
            "Run specguard:tester on domain-phone.md", "bare-name", cwd=self.repo_dir
        )

    def test_report_destination_path_not_mistaken_for_a_spec(self):
        # the report destination shares the .md suffix but lives outside
        # specs_dir, so it must not be extracted as a spec reference; an
        # edited, unapproved spec referenced in the same prompt must still
        # deny (proves the report path was ignored, not silently accepted).
        with open(os.path.join(self.repo_dir, "docs", "specs", "domain-phone.md"), "w") as f:
            f.write("rule one edited\n")
        code, out, err = self._run(
            "Запусти субагента specguard:tester на sample-shops/docs/specs/domain-phone.md, "
            "отчёт в .omc/research/tester/lab-domain-phone.md.",
            session_id="report-path",
            cwd=self.project_dir,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("domain-phone.md", reason)
        self.assertNotIn("lab-domain-phone", reason)

    def test_exact_live_run_prompt_passes_on_committed_spec(self):
        prompt = (
            "Запусти субагента specguard:tester на sample-shops/docs/specs/domain-phone.md, "
            "отчёт в .omc/research/tester/lab-domain-phone.md."
        )
        self._assert_passes(prompt, "live-run-prompt", cwd=self.project_dir)


if __name__ == "__main__":
    unittest.main()
