import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)

import helpers  # noqa: E402

S1 = "s1"
S2 = "s2"
X = "docs/specs/x.md"
A = "docs/specs/a.md"
B = "docs/specs/b.md"
FEATURE = {"version": 1, "modes": {"default": "feature"}}
FEATURE_OFF = {"version": 1, "modes": {"default": "feature", "approval_default": False}}
HARD = {"version": 1, "modes": {"default": "hard"}}
SIMPLE = {"version": 1, "modes": {"default": "simple"}}
FEATURE_QA = {"version": 1, "modes": {"default": "feature"}, "roles": {"tester": ["qa"]}}
GATE_LINES = (
    "specguard: approved",
    "specguard: nothing to approve",
    "specguard: approval not recorded",
    "specguard: could not read the transcript",
)


def sha7(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:7]


def marker(spec, content):
    return "specguard-approve {}@{}".format(spec, sha7(content))


def approved_line(spec, content):
    return "specguard: approved {} ({})".format(spec, sha7(content))


def assistant(*texts):
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": t} for t in texts]},
    }


def user_line(text="hi"):
    return {"type": "user", "message": {"role": "user", "content": text}}


def question(text, labels=("✓ Approve", "Not yet")):
    return {"question": text, "options": [{"label": label} for label in labels]}


def approval_question(markers, labels=("✓ Approve", "Not yet")):
    return question("Approve these specs? " + " ".join(markers), labels)


def msg(out):
    return out.get("systemMessage", "") if out else ""


class Env:
    def __init__(self, tc, config=FEATURE, committed=None, untracked=None):
        self.tc = tc
        tmp = tempfile.mkdtemp(prefix="ap-proj-")
        tc.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="ap-state-")
        tc.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        files = {"seed.txt": "seed\n"}
        files.update(committed or {})
        self.dir = helpers.make_project(tmp, config=config, files=files, git=True, commit=True)
        for rel, content in (untracked or {}).items():
            self.write(rel, content)

    def write(self, rel, content):
        full = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def transcript(self, lines):
        path = os.path.join(self.state_home, "transcript-{}.jsonl".format(len(os.listdir(self.state_home))))
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return path

    def call(self, event, payload):
        code, out, _ = helpers.run_hook(event, payload, self.dir, state_home=self.state_home)
        self.tc.assertEqual(code, 0)
        return out

    def launch(self, prompt=None, subagent_type="specguard:tester", session=S1, specs=(X,), **extra):
        if prompt is None:
            prompt = "Write the tests for " + " and ".join(specs)
        payload = helpers.pre_tool_use(session, "Agent", {"subagent_type": subagent_type, "prompt": prompt}, **extra)
        return self.call("PreToolUse", payload)

    def reason(self, out):
        self.tc.assertIsNotNone(out, "expected a refusal, got an allowed call")
        decision = out["hookSpecificOutput"]
        self.tc.assertEqual(decision["permissionDecision"], "deny")
        return decision["permissionDecisionReason"]

    def refuse(self, **kw):
        return self.reason(self.launch(**kw))

    def ctx(self, session=S1):
        out = self.call("UserPromptSubmit", helpers.user_prompt_submit(session, "hello"))
        if not out:
            return ""
        return out.get("hookSpecificOutput", {}).get("additionalContext", "")

    def tool(self, name, tool_input, session=S1, **extra):
        return self.call("PreToolUse", helpers.pre_tool_use(session, name, tool_input, **extra))

    def ask(self, questions, session=S1, **extra):
        return self.tool("AskUserQuestion", {"questions": questions}, session=session, **extra)

    def answer(self, questions, answers, session=S1, where="input"):
        if where == "input":
            payload = helpers.post_tool_use(
                session, "AskUserQuestion", {"questions": questions, "answers": answers}, {}
            )
        else:
            payload = helpers.post_tool_use(session, "AskUserQuestion", {"questions": questions}, {"answers": answers})
        return self.call("PostToolUse", payload)

    def say(self, text, session=S1, transcript=None, **extra):
        if transcript is not None:
            extra["transcript_path"] = transcript
        return self.call("UserPromptSubmit", helpers.user_prompt_submit(session, text, **extra))

    def register(self, agent_id, agent_type, session=S1):
        self.call("SubagentStart", helpers.subagent_start(session, agent_id, agent_type))

    def open_request(self, specs=(X,), session=S1):
        self.refuse(specs=specs, session=session)

    def approve_by_answer(self, specs=(X,), session=S1):
        """Opens requests for the current content of specs, answers the approval question with its check mark."""
        self.open_request(specs, session)
        markers = [marker(s, self.read(s)) for s in specs]
        q = approval_question(markers)
        out = self.answer([q], {q["question"]: "✓ Approve"}, session=session)
        for s in specs:
            self.tc.assertIn(approved_line(s, self.read(s)), msg(out))
        return out

    def read(self, rel):
        with open(os.path.join(self.dir, rel), encoding="utf-8") as f:
            return f.read()


class Base(unittest.TestCase):
    def env(self, config=FEATURE, committed=None, untracked=None):
        if untracked is None and committed is None:
            untracked = {X: "v1\n"}
        return Env(self, config, committed, untracked)


class TestAP1Scope(Base):
    def test_AP_1_launch_denied_for_each_tester_name(self):
        rows = (
            (FEATURE, "specguard:tester"),
            (FEATURE, "tester"),
            (FEATURE_QA, "qa"),
        )
        for config, name in rows:
            with self.subTest(subagent_type=name):
                env = self.env(config)
                self.assertIn("spec not approved", env.refuse(subagent_type=name))

    def test_AP_1_other_subagent_types_are_allowed(self):
        for name in ("general-purpose", "specguard:devils-advocate"):
            with self.subTest(subagent_type=name):
                env = self.env()
                self.assertIsNone(env.launch(subagent_type=name))

    def test_AP_1_simple_mode_allows_modified_spec(self):
        self.assertIsNone(self.env(SIMPLE).launch())

    def test_AP_1_feature_mode_with_approval_off_allows_modified_spec(self):
        self.assertIsNone(self.env(FEATURE_OFF).launch())

    def test_AP_1_hard_mode_never_allows_modified_spec(self):
        self.assertIsNotNone(self.env(HARD).launch())

    def test_AP_1_call_made_by_a_role_is_not_gated(self):
        env = self.env()
        out = env.launch(agent_type="specguard:tester", agent_id="ag-9")
        if out is not None:
            self.assertNotIn("spec not approved", env.reason(out))


class TestAP2SpecReference(Base):
    def test_AP_2_reference_forms_resolve_to_the_spec(self):
        env0 = self.env()
        absolute = os.path.join(env0.dir, X)
        rows = (
            ("relative path", "Write the tests for docs/specs/x.md"),
            ("dot slash path", "Write the tests for ./docs/specs/x.md"),
            ("bare name", "Write the tests for x.md"),
            ("backticks and comma", "see `docs/specs/x.md`, then write"),
            ("double quotes and period", 'Spec is "docs/specs/x.md".'),
            ("parentheses", "Spec (docs/specs/x.md) please"),
            ("trailing colon", "Spec docs/specs/x.md: write tests"),
        )
        for label, prompt in rows:
            with self.subTest(form=label):
                env = self.env()
                self.assertIn(marker(X, "v1\n"), env.refuse(prompt=prompt))
        with self.subTest(form="absolute path"):
            env = self.env()
            self.assertIn(marker(X, "v1\n"), env.refuse(prompt="Write the tests for " + os.path.join(env.dir, X)))

    def test_AP_2_non_spec_references_are_refused_by_name_the_spec_file(self):
        rows = (
            ("readme", "Use docs/specs/README.md"),
            ("md outside specs", "Use docs/notes.md"),
            ("missing spec", "Use docs/specs/missing.md"),
            ("no file", "Write some tests"),
        )
        for label, prompt in rows:
            with self.subTest(case=label):
                env = self.env(committed={"docs/notes.md": "n\n", "docs/specs/README.md": "r\n"}, untracked={X: "v1\n"})
                self.assertIn("name the spec file", env.refuse(prompt=prompt))

    def test_AP_2_missing_spec_opens_no_request(self):
        env = self.env()
        env.refuse(prompt="Use docs/specs/missing.md")
        self.assertNotIn("Open approval requests", env.ctx())


class TestAP3Pass(Base):
    def test_AP_3_spec_at_head_passes(self):
        env = self.env(committed={X: "v1\n"}, untracked={})
        self.assertIsNone(env.launch())

    def test_AP_3_edited_spec_without_approval_is_refused(self):
        env = self.env(committed={X: "v1\n"}, untracked={})
        env.write(X, "v1\nedit\n")
        self.assertIn("spec not approved", env.refuse())

    def test_AP_3_approved_spec_passes(self):
        env = self.env()
        env.approve_by_answer()
        self.assertIsNone(env.launch())

    def test_AP_3_approval_counts_in_another_session(self):
        env = self.env()
        env.approve_by_answer(session=S1)
        self.assertIsNone(env.launch(session=S2))

    def test_AP_3_approval_stops_after_the_spec_changes(self):
        env = self.env()
        env.approve_by_answer()
        env.write(X, "v2\n")
        self.assertIn(marker(X, "v2\n"), env.refuse())

    def test_AP_3_only_the_failing_spec_gets_a_marker(self):
        env = self.env(committed={A: "a1\n"}, untracked={B: "b1\n"})
        reason = env.refuse(specs=(A, B))
        self.assertIn(marker(B, "b1\n"), reason)
        self.assertNotIn("specguard-approve " + A, reason)


class TestAP4Refusal(Base):
    def test_AP_4_markers_follow_prompt_order(self):
        env = self.env(untracked={A: "a1\n", B: "b1\n"})
        reason = env.refuse(specs=(B, A))
        self.assertLess(reason.index(marker(B, "b1\n")), reason.index(marker(A, "a1\n")))

    def test_AP_4_marker_holds_first_seven_hex_of_sha256(self):
        env = self.env(untracked={X: "v1\n"})
        expected = "specguard-approve docs/specs/x.md@" + hashlib.sha256(b"v1\n").hexdigest()[:7]
        self.assertIn(expected, env.refuse())

    def test_AP_4_reason_tells_executor_to_pass_markers_from_a_subagent(self):
        reason = self.env().refuse()
        self.assertIn("spec not approved", reason)
        self.assertIn("AskUserQuestion", reason)
        self.assertIn("subagent", reason)


class TestAP5Requests(Base):
    def test_AP_5_refusal_opens_a_request(self):
        env = self.env()
        env.refuse()
        self.assertIn("Open approval requests - docs/specs/x.md", env.ctx())

    def test_AP_5_two_specs_are_listed_sorted(self):
        env = self.env(untracked={A: "a1\n", B: "b1\n"})
        env.refuse(specs=(B, A))
        self.assertIn("Open approval requests - docs/specs/a.md, docs/specs/b.md", env.ctx())

    def test_AP_5_repeated_launch_keeps_marker_and_single_request(self):
        env = self.env()
        first = env.refuse()
        second = env.refuse()
        self.assertIn(marker(X, "v1\n"), first)
        self.assertIn(marker(X, "v1\n"), second)
        ctx = env.ctx()
        self.assertIn("Open approval requests - docs/specs/x.md", ctx)
        self.assertEqual(ctx.count("docs/specs/x.md"), 1)

    def test_AP_5_launch_after_edit_replaces_the_request(self):
        env = self.env()
        env.refuse()
        env.write(X, "v2\n")
        second = env.refuse()
        self.assertIn(marker(X, "v2\n"), second)
        self.assertNotIn(marker(X, "v1\n"), second)
        self.assertEqual(env.ctx().count("docs/specs/x.md"), 1)

    def test_AP_5_no_request_without_a_spec_reference(self):
        env = self.env()
        env.refuse(prompt="Write some tests")
        self.assertNotIn("Open approval requests", env.ctx())

    def test_AP_5_context_is_silent_when_nothing_is_open(self):
        self.assertNotIn("Open approval requests", self.env().ctx())


class TestAP6PreFilled(Base):
    def test_AP_6_answers_field_on_unrelated_question_is_refused(self):
        env = self.env()
        out = env.tool(
            "AskUserQuestion",
            {"questions": [question("Which colour?", ("Red", "Blue"))], "answers": {"q": "yes"}},
        )
        self.assertIn("pre-filled answers", env.reason(out))

    def test_AP_6_answers_field_on_approval_question_is_refused(self):
        env = self.env()
        env.open_request()
        q = approval_question([marker(X, "v1\n")])
        out = env.tool("AskUserQuestion", {"questions": [q], "answers": {q["question"]: "✓ Approve"}})
        self.assertIn("pre-filled answers", env.reason(out))


class TestAP7Structure(Base):
    def open_x(self):
        env = self.env()
        env.open_request()
        return env

    def test_AP_7_sound_question_is_allowed(self):
        env = self.open_x()
        self.assertIsNone(env.ask([approval_question([marker(X, "v1\n")])]))

    def test_AP_7_broken_option_sets_are_refused_with_check_mark_reason(self):
        rows = (
            ("no check mark", ("Approve", "Not yet")),
            ("two check marks", ("✓ Approve", "✓ Also")),
            ("single option", ("✓ Approve",)),
        )
        for label, labels in rows:
            with self.subTest(case=label):
                env = self.open_x()
                out = env.ask([approval_question([marker(X, "v1\n")], labels)])
                self.assertIn("✓", env.reason(out))

    def test_AP_7_question_missing_another_requests_marker_is_refused(self):
        env = self.env(untracked={A: "a1\n", B: "b1\n"})
        env.open_request((A, B))
        out = env.ask([approval_question([marker(A, "a1\n")])])
        self.assertIn(marker(B, "b1\n"), env.reason(out))

    def test_AP_7_question_with_all_markers_is_allowed(self):
        env = self.env(untracked={A: "a1\n", B: "b1\n"})
        env.open_request((A, B))
        self.assertIsNone(env.ask([approval_question([marker(A, "a1\n"), marker(B, "b1\n")])]))

    def test_AP_7_one_broken_question_refuses_the_whole_call(self):
        env = self.open_x()
        out = env.ask(
            [
                question("Which colour?", ("Red", "Blue")),
                approval_question([marker(X, "v1\n")], ("Approve", "Not yet")),
            ]
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")


class TestAP8Unchecked(Base):
    def test_AP_8_question_without_marker_is_allowed(self):
        env = self.env()
        self.assertIsNone(env.ask([question("Which colour?", ("Red", "Blue"))]))

    def test_AP_8_marker_of_spec_without_open_request_is_allowed(self):
        env = self.env()
        self.assertIsNone(env.ask([approval_question([marker(X, "v1\n")], ("Yes", "No"))]))

    def test_AP_8_marker_with_wrong_hash_is_allowed(self):
        env = self.env()
        env.open_request()
        self.assertIsNone(env.ask([approval_question([marker(X, "other\n")], ("Yes", "No"))]))


class TestAP9Approve(Base):
    def test_AP_9_check_mark_answer_in_tool_input_approves(self):
        env = self.env()
        env.open_request()
        q = approval_question([marker(X, "v1\n")])
        out = env.answer([q], {q["question"]: "✓ Approve"}, where="input")
        self.assertIn("specguard: approved docs/specs/x.md ({})".format(sha7("v1\n")), msg(out))
        self.assertIsNone(env.launch())
        self.assertNotIn("Open approval requests", env.ctx())

    def test_AP_9_check_mark_answer_in_tool_response_approves(self):
        env = self.env()
        env.open_request()
        q = approval_question([marker(X, "v1\n")])
        out = env.answer([q], {q["question"]: "✓ Approve"}, where="response")
        self.assertIn(approved_line(X, "v1\n"), msg(out))
        self.assertIsNone(env.launch())
        self.assertNotIn("Open approval requests", env.ctx())

    def test_AP_9_answer_keyed_by_another_question_text_approves_nothing(self):
        env = self.env()
        env.open_request()
        q = approval_question([marker(X, "v1\n")])
        out = env.answer([q], {"A different question text": "✓ Approve"})
        self.assertNotIn("specguard: approved", msg(out))
        self.assertIn(marker(X, "v1\n"), env.refuse())


class TestAP10Decline(Base):
    def test_AP_10_other_label_closes_without_approval(self):
        env = self.env()
        env.open_request()
        q = approval_question([marker(X, "v1\n")])
        out = env.answer([q], {q["question"]: "Not yet"})
        self.assertIn("specguard: request closed for docs/specs/x.md", msg(out))
        self.assertNotIn("specguard: approved", msg(out))
        self.assertNotIn("Open approval requests", env.ctx())
        self.assertIn(marker(X, "v1\n"), env.refuse())
        self.assertIn("Open approval requests - docs/specs/x.md", env.ctx())


class TestAP11Stale(Base):
    def test_AP_11_answer_to_old_hash_changes_nothing(self):
        env = self.env()
        env.open_request()
        q = approval_question([marker(X, "v1\n")])
        env.write(X, "v2\n")
        out = env.answer([q], {q["question"]: "✓ Approve"})
        self.assertNotIn("specguard: approved", msg(out))
        self.assertNotIn("specguard: request closed", msg(out))
        self.assertIn("Open approval requests - docs/specs/x.md", env.ctx())
        self.assertIn(marker(X, "v2\n"), env.refuse())

    def test_AP_11_answer_map_without_the_question_changes_nothing(self):
        env = self.env()
        env.open_request()
        q = approval_question([marker(X, "v1\n")])
        out = env.answer([q], {})
        self.assertNotIn("specguard: approved", msg(out))
        self.assertIn(marker(X, "v1\n"), env.refuse())

    def test_AP_11_question_without_marker_changes_nothing(self):
        env = self.env()
        env.open_request()
        q = question("Which colour?", ("✓ Red", "Blue"))
        out = env.answer([q], {q["question"]: "✓ Red"})
        self.assertNotIn("specguard: approved", msg(out))
        self.assertIn("Open approval requests - docs/specs/x.md", env.ctx())

    def test_AP_11_answer_while_no_request_is_open_prints_no_approval(self):
        env = self.env()
        q = approval_question([marker(X, "v1\n")])
        out = env.answer([q], {q["question"]: "✓ Approve"})
        self.assertNotIn("specguard: approved", msg(out))
        self.assertIn("spec not approved", env.refuse())


class TestAP12Several(Base):
    def test_AP_12_one_answer_approves_every_marker_in_the_question(self):
        env = self.env(untracked={A: "a1\n", B: "b1\n"})
        env.open_request((A, B))
        q = approval_question([marker(A, "a1\n"), marker(B, "b1\n")])
        out = env.answer([q], {q["question"]: "✓ Approve"})
        self.assertIn(approved_line(A, "a1\n"), msg(out))
        self.assertIn(approved_line(B, "b1\n"), msg(out))
        self.assertIsNone(env.launch(specs=(A, B)))


class TestAP13TextApproval(Base):
    def opened(self):
        env = self.env()
        env.open_request()
        path = env.transcript([assistant("Please approve " + marker(X, "v1\n"))])
        return env, path

    def test_AP_13_approval_words_count(self):
        for text in ("approve", "Approve.", "ok", "yes ok", "да апрув", "схвалюю"):
            with self.subTest(prompt=text):
                env, path = self.opened()
                out = env.say(text, transcript=path)
                self.assertIn(approved_line(X, "v1\n"), msg(out))

    def test_AP_13_lone_ambiguous_word_prints_nothing_and_keeps_request(self):
        for text in ("yes", "да", "так"):
            with self.subTest(prompt=text):
                env, path = self.opened()
                out = env.say(text, transcript=path)
                for line in GATE_LINES:
                    self.assertNotIn(line, msg(out))
                self.assertIn("Open approval requests - docs/specs/x.md", env.ctx())

    def test_AP_13_speech_that_is_not_approval_is_ignored(self):
        for text in ("approve?", "approve the spec please", "+", "👍", "<task-notification>"):
            with self.subTest(prompt=text):
                env, path = self.opened()
                out = env.say(text, transcript=path)
                self.assertNotIn("specguard: approved", msg(out))
                self.assertIn(marker(X, "v1\n"), env.refuse())

    def test_AP_13_prompt_of_a_role_session_is_not_approval(self):
        env, path = self.opened()
        out = env.say("approve", transcript=path, agent_type="specguard:tester", agent_id="ag-1")
        self.assertNotIn("specguard: approved", msg(out))
        self.assertIn(marker(X, "v1\n"), env.refuse())


class TestAP14TextTarget(Base):
    def test_AP_14_marker_in_last_turn_approves_and_launch_passes(self):
        env = self.env()
        env.open_request()
        path = env.transcript([assistant("Approve? " + marker(X, "v1\n"))])
        out = env.say("approve", transcript=path)
        self.assertIn(approved_line(X, "v1\n"), msg(out))
        self.assertIsNone(env.launch())

    def test_AP_14_only_the_request_named_in_last_turn_is_approved(self):
        env = self.env(untracked={A: "a1\n", B: "b1\n"})
        env.open_request((A, B))
        path = env.transcript([assistant("Approve? " + marker(A, "a1\n"))])
        out = env.say("approve", transcript=path)
        self.assertIn(approved_line(A, "a1\n"), msg(out))
        self.assertNotIn(approved_line(B, "b1\n"), msg(out))
        self.assertIsNone(env.launch(specs=(A,)))
        self.assertIn(marker(B, "b1\n"), env.refuse(specs=(B,)))

    def test_AP_14_marker_in_earlier_turn_does_not_count(self):
        env = self.env()
        env.open_request()
        path = env.transcript([assistant("Approve? " + marker(X, "v1\n")), user_line(), assistant("Plain text")])
        out = env.say("approve", transcript=path)
        self.assertIn("specguard: approval not recorded, no marker in the last message", msg(out))
        self.assertIn(marker(X, "v1\n"), env.refuse())

    def test_AP_14_text_blocks_of_last_turn_are_read_together(self):
        env = self.env(untracked={A: "a1\n", B: "b1\n"})
        env.open_request((A, B))
        path = env.transcript([assistant("First " + marker(A, "a1\n"), "Second " + marker(B, "b1\n"))])
        out = env.say("approve", transcript=path)
        self.assertIn(approved_line(A, "a1\n"), msg(out))
        self.assertIn(approved_line(B, "b1\n"), msg(out))

    def test_AP_14_marker_with_another_hash_does_not_match(self):
        env = self.env()
        env.open_request()
        path = env.transcript([assistant("Approve? " + marker(X, "old\n"))])
        out = env.say("approve", transcript=path)
        self.assertNotIn("specguard: approved", msg(out))
        self.assertIn(marker(X, "v1\n"), env.refuse())

    def test_AP_14_spec_edited_after_request_is_not_approved(self):
        env = self.env()
        env.open_request()
        path = env.transcript([assistant("Approve? " + marker(X, "v1\n"))])
        env.write(X, "v2\n")
        out = env.say("approve", transcript=path)
        self.assertNotIn("specguard: approved", msg(out))
        self.assertIn(marker(X, "v2\n"), env.refuse())

    def test_AP_14_marker_cut_across_two_text_blocks_does_not_match(self):
        env = self.env()
        env.open_request()
        full = marker(X, "v1\n")
        cut = len(full) // 2
        path = env.transcript([assistant("Approve? " + full[:cut], full[cut:])])
        out = env.say("approve", transcript=path)
        self.assertNotIn("specguard: approved", msg(out))
        self.assertIn(marker(X, "v1\n"), env.refuse())

    def test_AP_14_user_line_after_the_marked_turn_is_skipped(self):
        env = self.env()
        env.open_request()
        path = env.transcript([assistant("Approve? " + marker(X, "v1\n")), user_line("later")])
        out = env.say("approve", transcript=path)
        self.assertIn(approved_line(X, "v1\n"), msg(out))


class TestAP15Explanations(Base):
    def test_AP_15_no_open_request_says_so(self):
        env = self.env()
        out = env.say("approve")
        self.assertIn("specguard: nothing to approve, no request is open", msg(out))

    def test_AP_15_transcript_missing_or_without_assistant_text(self):
        for label in ("missing file", "no assistant text"):
            with self.subTest(case=label):
                env = self.env()
                env.open_request()
                if label == "missing file":
                    path = os.path.join(env.state_home, "no-such-transcript.jsonl")
                else:
                    path = env.transcript([user_line()])
                out = env.say("approve", transcript=path)
                self.assertIn("specguard: could not read the transcript, approval not recorded", msg(out))

    def test_AP_15_last_turn_without_marker_says_so(self):
        env = self.env()
        env.open_request()
        path = env.transcript([assistant("Nothing marked here")])
        out = env.say("ok", transcript=path)
        self.assertIn("specguard: approval not recorded, no marker in the last message", msg(out))

    def test_AP_15_mode_trigger_word_stays_silent_in_every_case(self):
        for word in ("go", "го"):
            with self.subTest(word=word, case="no request"):
                env = self.env()
                out = env.say(word)
                for line in GATE_LINES:
                    self.assertNotIn(line, msg(out))
            with self.subTest(word=word, case="transcript missing"):
                env = self.env()
                env.open_request()
                out = env.say(word, transcript=os.path.join(env.state_home, "none.jsonl"))
                for line in GATE_LINES:
                    self.assertNotIn(line, msg(out))
            with self.subTest(word=word, case="no marker"):
                env = self.env()
                env.open_request()
                out = env.say(word, transcript=env.transcript([assistant("plain")]))
                for line in GATE_LINES:
                    self.assertNotIn(line, msg(out))


class TestAP16Revoke(Base):
    def test_AP_16_revoke_phrases_take_the_approval_back(self):
        rows = (
            "revoke approval",
            "Revoke Approval",
            "отзываю апрув",
            "отмени апрув",
            "відкликаю апрув, please",
        )
        for text in rows:
            with self.subTest(prompt=text):
                env = self.env()
                env.approve_by_answer()
                out = env.say(text)
                self.assertIn("specguard: approval revoked - docs/specs/x.md ({})".format(sha7("v1\n")), msg(out))
                self.assertIn(marker(X, "v1\n"), env.refuse())

    def test_AP_16_text_approval_is_revoked_too(self):
        env = self.env()
        env.open_request()
        path = env.transcript([assistant("Approve? " + marker(X, "v1\n"))])
        env.say("approve", transcript=path)
        self.assertIsNone(env.launch())
        out = env.say("Отзываю апрув")
        self.assertIn("specguard: approval revoked - docs/specs/x.md", msg(out))
        self.assertIn("spec not approved", env.refuse())

    def test_AP_16_revoke_after_the_spec_was_edited_still_prints_its_line(self):
        env = self.env()
        env.approve_by_answer()
        env.write(X, "v2\n")
        out = env.say("revoke approval")
        self.assertIn("specguard: approval revoked - docs/specs/x.md ({})".format(sha7("v1\n")), msg(out))
        self.assertNotIn("nothing to revoke", msg(out))

    def test_AP_16_question_is_not_a_revoke(self):
        env = self.env()
        env.approve_by_answer()
        out = env.say("revoke approval?")
        self.assertNotIn("specguard: approval revoked", msg(out))
        self.assertIsNone(env.launch())

    def test_AP_16_every_approval_of_the_session_is_revoked(self):
        env = self.env(untracked={A: "a1\n", B: "b1\n"})
        env.approve_by_answer((A, B))
        out = env.say("revoke approval")
        self.assertIn("specguard: approval revoked - docs/specs/a.md ({})".format(sha7("a1\n")), msg(out))
        self.assertIn("specguard: approval revoked - docs/specs/b.md ({})".format(sha7("b1\n")), msg(out))

    def test_AP_16_second_revoke_finds_nothing(self):
        env = self.env()
        env.approve_by_answer()
        env.say("revoke approval")
        out = env.say("revoke approval")
        self.assertIn("specguard: nothing to revoke", msg(out))
        self.assertNotIn("approval revoked", msg(out))

    def test_AP_16_approval_of_another_session_stays(self):
        env = self.env()
        env.approve_by_answer(session=S1)
        out = env.say("revoke approval", session=S2)
        self.assertIn("specguard: nothing to revoke", msg(out))
        self.assertIsNone(env.launch(session=S2))

    def test_AP_16_revoke_of_spec_at_head_leaves_launch_open(self):
        env = self.env(committed={X: "v1\n"}, untracked={})
        out = env.say("revoke approval")
        self.assertIn("specguard: nothing to revoke", msg(out))
        self.assertIsNone(env.launch())


class TestAP17OptOut(Base):
    def test_AP_17_opt_out_phrases_turn_approval_off(self):
        for text in ("no approval", "No Approval", "без апрува", "Без апруву."):
            with self.subTest(prompt=text):
                env = self.env()
                out = env.say(text)
                self.assertIn("specguard: mode feature, approval off", msg(out))
                self.assertIsNone(env.launch())

    def test_AP_17_hard_mode_refuses_the_opt_out(self):
        env = self.env(HARD)
        out = env.say("no approval")
        self.assertIn("specguard: no-approval refused, hard mode always requires approval", msg(out))
        self.assertIsNotNone(env.launch())


    def test_AP_17_more_opt_out_phrases_in_feature(self):
        for text in ("без аппрува", "без апруфа", "NO APPROVAL!"):
            with self.subTest(prompt=text):
                env = self.env()
                self.assertIn("specguard: mode feature, approval off", msg(env.say(text)))
                self.assertIsNone(env.launch())

    def test_AP_17_visual_plus_feature_works_as_feature(self):
        env = self.env()
        env.call("UserPromptExpansion", helpers.user_prompt_expansion(S1, "specguard:mode", "visual+feature"))
        self.assertIn("specguard: mode visual+feature, approval off", msg(env.say("no approval")))
        self.assertIsNone(env.launch())

    def test_AP_17_modes_without_feature_or_hard_change_nothing(self):
        rows = (
            ("simple", SIMPLE, "no approval"),
            ("simple", SIMPLE, "Без апруву."),
            ("visual", {"version": 1, "modes": {"default": "visual"}}, "без апрува"),
        )
        for label, config, text in rows:
            with self.subTest(mode=label, prompt=text):
                env = self.env(config)
                env.say("hello")
                before = self.state_snapshot(env)
                out = env.say(text)
                self.assertIn("specguard: approval applies only in feature or hard mode", msg(out))
                self.assertNotIn("mode " + label, msg(out))
                self.assertNotIn("approval off", msg(out))
                self.assertEqual(self.state_snapshot(env), before)

    def test_AP_17_simple_mode_opt_out_leaves_no_trace_for_later_feature(self):
        env = self.env(SIMPLE)
        env.say("no approval")
        out = env.say("go feature spec")
        self.assertIn("specguard: mode feature, approval on", msg(out))
        self.assertIsNotNone(env.launch())

    def test_AP_17_simple_mode_status_line_stays_simple(self):
        env = self.env(SIMPLE)
        out = env.say("no approval")
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("Active specguard mode is simple.", ctx)

    def state_snapshot(self, env):
        snap = {}
        for root, _, names in os.walk(env.state_home):
            for name in names:
                if name == "log.jsonl":
                    continue
                path = os.path.join(root, name)
                with open(path, "rb") as f:
                    snap[os.path.relpath(path, env.state_home)] = f.read()
        return snap


class TestAP18RunningRoles(Base):
    def send(self, env, to="ag-1", text="Please continue with the tests"):
        return env.tool("SendMessage", {"to": to, "message": text})

    def test_AP_18_message_to_registered_tester_or_advocate_is_refused(self):
        rows = (
            (FEATURE, "specguard:tester"),
            (FEATURE, "tester"),
            (FEATURE, "specguard:devils-advocate"),
            (FEATURE, "devils-advocate"),
            (HARD, "specguard:devils-advocate"),
            (HARD, "specguard:tester"),
            (FEATURE_QA, "qa"),
        )
        for config, agent_type in rows:
            with self.subTest(agent_type=agent_type, mode=config["modes"]["default"]):
                env = self.env(config)
                env.register("ag-1", agent_type)
                self.assertIn("new Agent launch", env.reason(self.send(env)))

    def test_AP_18_modes_without_the_gate_allow_the_message(self):
        for label, config in (("approval off", FEATURE_OFF), ("simple", SIMPLE)):
            with self.subTest(mode=label):
                env = self.env(config)
                env.register("ag-1", "specguard:tester")
                self.assertIsNone(self.send(env))

    def test_AP_18_other_agent_type_is_allowed(self):
        env = self.env()
        env.register("ag-1", "general-purpose")
        self.assertIsNone(self.send(env))

    def test_AP_18_unknown_id_is_allowed(self):
        self.assertIsNone(self.send(self.env(), to="never-registered"))


class TestAP19Steering(Base):
    def test_AP_19_steering_texts_are_refused_on_every_channel(self):
        rows = (
            ("SendMessage", "message", "go feature spec"),
            ("SendMessage", "message", "go feature spec now please"),
            ("ScheduleWakeup", "prompt", "go hard mode and continue"),
            ("CronCreate", "prompt", "го хардмод"),
            ("CronCreate", "prompt", "го фічспек"),
            ("SendMessage", "message", "no approval"),
            ("SendMessage", "message", "без апрува"),
            ("SendMessage", "message", "revoke approval"),
            ("SendMessage", "message", "approve"),
            ("CronCreate", "prompt", "yes ok"),
        )
        for tool, field, text in rows:
            with self.subTest(tool=tool, text=text):
                env = self.env()
                tool_input = {field: text}
                if tool == "SendMessage":
                    tool_input["to"] = "nobody"
                elif tool == "CronCreate":
                    tool_input["cron"] = "*/5 * * * *"
                else:
                    tool_input["delaySeconds"] = 60
                out = env.tool(tool, tool_input)
                self.assertIn("may not carry mode or approval phrases", env.reason(out))

    def test_AP_19_simple_mode_refuses_mode_phrase_too(self):
        env = self.env(SIMPLE)
        out = env.tool("SendMessage", {"to": "nobody", "message": "go feature spec"})
        self.assertIn("may not carry mode or approval phrases", env.reason(out))

    def test_AP_19_ordinary_texts_are_allowed(self):
        rows = (
            ("SendMessage", "Please continue with the tests"),
            ("SendMessage", "then go feature spec later"),
            ("SendMessage", "approve the spec please"),
            ("SendMessage", "yes"),
            ("ScheduleWakeup", "check the build"),
        )
        for tool, text in rows:
            with self.subTest(tool=tool, text=text):
                env = self.env()
                if tool == "SendMessage":
                    tool_input = {"to": "nobody", "message": text}
                else:
                    tool_input = {"delaySeconds": 60, "prompt": text}
                self.assertIsNone(env.tool(tool, tool_input))


if __name__ == "__main__":
    unittest.main()
