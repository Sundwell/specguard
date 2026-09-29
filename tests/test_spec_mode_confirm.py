import json
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402

CONFIG = {"version": 1, "modes": {"default": "simple", "approval_default": True}}
YES = "✓ Switch"
STAY = "Stay"
REFUSAL = "specguard: no-approval refused, hard mode always requires approval"
SPEC_MARKER = "specguard-approve docs/specs/x.md@aaaaaaa"


def question_text(marker_set="feature", suffix=""):
    return "Switch? specguard-mode {}{}".format(marker_set, suffix)


def ask_input(text, labels=(YES, STAY)):
    return {"questions": [{"question": text, "options": [{"label": l} for l in labels]}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sg-mc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.counter = 0
        self.reset()

    def reset(self, config=CONFIG):
        self.counter += 1
        self.project = helpers.make_project(
            os.path.join(self.tmp, "proj{}".format(self.counter)), config=config
        )
        self.state_home = os.path.join(self.tmp, "state{}".format(self.counter))
        os.makedirs(self.state_home)
        self.sid = "sess{}".format(self.counter)

    def state_dir(self):
        key = self.project.replace("/", "-")
        return os.path.join(self.state_home, "specguard", key)

    def write_session(self, requests=None, pending=0):
        d = os.path.join(self.state_dir(), "sessions")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, self.sid + ".json"), "w", encoding="utf-8") as f:
            json.dump({"requests": requests or {}, "visual": {"pending": pending}}, f)

    def hook(self, event, payload):
        code, out, err = helpers.run_hook(event, payload, self.project, state_home=self.state_home)
        self.assertEqual(code, 0, err)
        return out

    def pre(self, tool_input, **extra):
        return self.hook("PreToolUse", helpers.pre_tool_use(self.sid, "AskUserQuestion", tool_input, **extra))

    def post(self, tool_input, tool_response=None, **extra):
        return self.hook(
            "PostToolUse",
            helpers.post_tool_use(self.sid, "AskUserQuestion", tool_input, tool_response or {}, **extra),
        )

    def answer(self, text, label, labels=(YES, STAY), where="input", **extra):
        tin = ask_input(text, labels)
        answers = {text: label}
        if where == "input":
            tin["answers"] = answers
            return self.post(tin, {}, **extra)
        return self.post(tin, {"answers": answers}, **extra)

    def context(self, out):
        return out["hookSpecificOutput"]["additionalContext"]

    def status(self):
        out = self.hook("UserPromptSubmit", helpers.user_prompt_submit(self.sid, "hello"))
        ctx = self.context(out)
        marker = "Active specguard mode is "
        return ctx[ctx.rindex(marker):].strip()

    def transcript(self, lines):
        path = os.path.join(self.tmp, "tr{}.jsonl".format(self.counter))
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return path

    def assistant(self, *texts):
        return {"message": {"role": "assistant", "content": [{"type": "text", "text": t} for t in texts]}}

    def reply(self, prompt, path, **extra):
        return self.hook(
            "UserPromptSubmit", helpers.user_prompt_submit(self.sid, prompt, transcript_path=path, **extra)
        )

    def text_switch(self, marker_set, prompt="yes"):
        path = self.transcript([self.assistant("Switch? specguard-mode " + marker_set)])
        return self.reply(prompt, path)

    def log(self):
        p = os.path.join(self.state_dir(), "log.jsonl")
        with open(p, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def assertPromptUntouched(self, out):
        # a plain prompt still prints the status line, so "prints nothing" means no message and no rules
        self.assertNotIn("systemMessage", out)
        self.assertEqual(self.context(out), "Active specguard mode is simple.")

    def assertDenied(self, out, *needles):
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        self.assertEqual(spec["permissionDecision"], "deny")
        self.assertTrue(spec["permissionDecisionReason"].startswith("specguard: "))
        for n in needles:
            self.assertIn(n, spec["permissionDecisionReason"])


class TestQuestionStructure(Base):
    def test_MC_1_allowed_questions(self):
        cases = [
            ("one check option", question_text(), (YES, STAY)),
            ("no marker no check", "Do you like it?", ("Yes", "No")),
            ("no marker with checks", "Do you like it?", ("✓ A", "✓ B")),
            ("no-approval combined set", question_text("visual+feature", " no-approval"), ("✓ Go", "No")),
        ]
        for name, text, labels in cases:
            with self.subTest(name):
                self.reset()
                self.assertIsNone(self.pre(ask_input(text, labels)))

    def test_MC_1_denied_questions(self):
        cases = [
            ("no check", ("Switch", STAY)),
            ("two checks", ("✓ Yes", "✓ Maybe", STAY)),
            ("single option", (YES,)),
            ("no options", ()),
        ]
        for name, labels in cases:
            with self.subTest(name):
                self.reset()
                self.assertDenied(self.pre(ask_input(question_text(), labels)), "✓")

    def test_MC_2_prefilled_answers_denied(self):
        for name, text in (("with marker", question_text()), ("without marker", "Do you like it?")):
            with self.subTest(name):
                self.reset()
                tin = ask_input(text)
                tin["answers"] = {text: YES}
                self.assertDenied(self.pre(tin), "pre-filled answers")

    def test_MC_3_mixed_markers_denied(self):
        with self.subTest("open spec request"):
            self.reset()
            self.write_session(requests={"docs/specs/x.md": {"sha256": "a" * 64}})
            out = self.pre(ask_input(question_text() + " " + SPEC_MARKER))
            self.assertDenied(out, "may not mix")
        with self.subTest("pending visual"):
            self.reset()
            self.write_session(pending=3)
            out = self.pre(ask_input(question_text() + " specguard-approve visual@3"))
            self.assertDenied(out, "may not mix")

    def test_MC_3_spec_marker_without_open_request_allowed(self):
        self.write_session()
        self.assertIsNone(self.pre(ask_input(question_text() + " " + SPEC_MARKER)))


class TestAnswerSwitch(Base):
    def test_MC_4_switch_and_status_line(self):
        cases = [
            ("feature", "feature", "Active specguard mode is feature, approval on."),
            ("hard", "hard", "Active specguard mode is hard, approval on."),
            ("visual+feature", "visual+feature", "Active specguard mode is visual+feature, approval on."),
            ("feature no-approval", "feature", "Active specguard mode is feature, approval off."),
            ("visual", "visual", "Active specguard mode is visual."),
        ]
        for marker_set, shown, status in cases:
            with self.subTest(marker_set):
                self.reset()
                out = self.answer(question_text(marker_set), YES)
                self.assertEqual(out["systemMessage"], "specguard: mode {} (confirmed by you)".format(shown))
                self.assertEqual(self.status(), status)

    def test_MC_4_simple_from_feature_session(self):
        self.answer(question_text("feature"), YES)
        self.assertEqual(self.status(), "Active specguard mode is feature, approval on.")
        out = self.answer(question_text("simple"), YES)
        self.assertEqual(out["systemMessage"], "specguard: mode simple (confirmed by you)")
        self.assertEqual(self.status(), "Active specguard mode is simple.")

    def test_MC_4_hard_no_approval_refused(self):
        out = self.answer(question_text("hard", " no-approval"), YES)
        lines = out["systemMessage"].splitlines()
        self.assertEqual(lines, ["specguard: mode hard (confirmed by you)", REFUSAL])
        self.assertEqual(self.status(), "Active specguard mode is hard, approval on.")

    def test_MC_4_approval_default_false_gives_off(self):
        self.reset(config={"version": 1, "modes": {"default": "simple", "approval_default": False}})
        self.answer(question_text("feature"), YES)
        self.assertEqual(self.status(), "Active specguard mode is feature, approval off.")

    def test_MC_4_approval_default_absent_gives_on(self):
        self.reset(config={"version": 1, "modes": {"default": "simple"}})
        self.answer(question_text("feature"), YES)
        self.assertEqual(self.status(), "Active specguard mode is feature, approval on.")

    def test_MC_4_answers_only_in_tool_response(self):
        out = self.answer(question_text("feature"), YES, where="response")
        self.assertEqual(out["systemMessage"], "specguard: mode feature (confirmed by you)")
        self.assertEqual(self.status(), "Active specguard mode is feature, approval on.")

    def test_MC_5_rules_delivered(self):
        cases = [
            ("hard", ["Hard mode adds."], []),
            ("feature", ["Feature mode rules."], ["Hard mode adds."]),
            ("visual", ["Visual mode rules."], ["Hard mode adds."]),
            ("simple", [], ["mode rules.", "Hard mode adds."]),
        ]
        for marker_set, present, absent in cases:
            with self.subTest(marker_set):
                self.reset()
                out = self.answer(question_text(marker_set), YES)
                self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
                ctx = self.context(out)
                for p in present:
                    self.assertIn(p, ctx)
                for a in absent:
                    self.assertNotIn(a, ctx)

    def test_MC_6_other_answers_change_nothing(self):
        for label in (STAY, "something else entirely"):
            with self.subTest(label):
                self.reset()
                out = self.answer(question_text("feature"), label)
                self.assertEqual(out, {"systemMessage": "specguard: mode unchanged"})
                self.assertEqual(self.status(), "Active specguard mode is simple.")

    def test_MC_7_invalid_sets_not_switched(self):
        for marker_set in ("simple+feature", "turbo", "hard+feature", "feature+hard"):
            with self.subTest(marker_set):
                self.reset()
                out = self.answer(question_text(marker_set), YES)
                self.assertTrue(out["systemMessage"].startswith("specguard: mode not switched,"))
                self.assertIn("is not a valid combination", out["systemMessage"])
                self.assertEqual(self.status(), "Active specguard mode is simple.")

    def test_MC_8_unrelated_posts_print_nothing(self):
        text = question_text("hard")
        with self.subTest("no answers anywhere"):
            self.reset()
            self.assertIsNone(self.post(ask_input(text), {}))
            self.assertEqual(self.status(), "Active specguard mode is simple.")
        with self.subTest("empty map"):
            self.reset()
            tin = ask_input(text)
            tin["answers"] = {}
            self.assertIsNone(self.post(tin, {"answers": {}}))
            self.assertEqual(self.status(), "Active specguard mode is simple.")
        with self.subTest("keyed by other text"):
            self.reset()
            tin = ask_input(text)
            tin["answers"] = {"some other question": YES}
            self.assertIsNone(self.post(tin, {}))
            self.assertEqual(self.status(), "Active specguard mode is simple.")
        with self.subTest("check answer without marker"):
            self.reset()
            self.assertIsNone(self.answer("Do you like it?", YES))
            self.assertEqual(self.status(), "Active specguard mode is simple.")

    def test_MC_9_role_sessions_switch_nothing(self):
        for role in ("specguard:tester", "specguard:devils-advocate"):
            with self.subTest(role):
                self.reset()
                self.assertIsNone(self.answer(question_text("hard"), YES, agent_type=role))
                self.assertEqual(self.status(), "Active specguard mode is simple.")


class TestTextFallback(Base):
    def test_MC_10_confirm_words_switch(self):
        replies = ["yes", "y", "ok", "+", "да", "так", "ок", "го", "так точно", "Да.", "yes!", "да го", "yes ok"]
        for reply in replies:
            with self.subTest(reply):
                self.reset()
                out = self.text_switch("feature", reply)
                self.assertEqual(out["systemMessage"], "specguard: mode feature (confirmed by you)")
                ctx = self.context(out)
                self.assertIn("Feature mode rules.", ctx)
                self.assertTrue(ctx.endswith("Active specguard mode is feature, approval on."))
                self.assertEqual(self.status(), "Active specguard mode is feature, approval on.")

    def test_MC_10_remaining_confirm_words(self):
        for reply in ("okay", "go", "approve", "ага", "угу", "давай", "апрув"):
            with self.subTest(reply):
                self.reset()
                out = self.text_switch("feature", reply)
                self.assertEqual(out["systemMessage"], "specguard: mode feature (confirmed by you)")
                self.assertIn("Feature mode rules.", self.context(out))

    def test_MC_10_approval_word_without_open_request_adds_no_line(self):
        for reply in ("ok", "approve", "апрув"):
            with self.subTest(reply):
                self.reset()
                self.write_session()
                out = self.text_switch("feature", reply)
                self.assertEqual(out["systemMessage"], "specguard: mode feature (confirmed by you)")
                self.assertEqual(len(out["systemMessage"].splitlines()), 1)

    def test_MC_10_combined_set_status_line(self):
        out = self.text_switch("visual+feature", "давай")
        self.assertIn("Active specguard mode is visual+feature", self.context(out))
        self.assertEqual(self.status(), "Active specguard mode is visual+feature, approval on.")

    def test_MC_11_unconfirmable_replies_print_nothing(self):
        replies = [
            "да нет", "не го", "yes please", "да, покажи правила сначала", "yes yes yes",
            "да?", "<task-notification>ok</task-notification>", "погоди, го",
        ]
        for reply in replies:
            with self.subTest(reply):
                self.reset()
                self.assertPromptUntouched(self.text_switch("feature", reply))
                self.assertEqual(self.status(), "Active specguard mode is simple.")

    def test_MC_11_role_session(self):
        path = self.transcript([self.assistant(question_text("feature"))])
        out = self.reply("yes", path, agent_type="specguard:tester")
        self.assertTrue(out is None or "systemMessage" not in out)
        self.assertEqual(self.status(), "Active specguard mode is simple.")

    def test_MC_11_marker_only_in_earlier_assistant_text(self):
        path = self.transcript([self.assistant(question_text("feature")), self.assistant("Anything else?")])
        self.assertPromptUntouched(self.reply("yes", path))
        self.assertEqual(self.status(), "Active specguard mode is simple.")

    def test_MC_11_transcript_problems(self):
        with self.subTest("missing file"):
            self.reset()
            self.assertPromptUntouched(self.reply("yes", os.path.join(self.tmp, "nope.jsonl")))
        with self.subTest("unreadable"):
            self.reset()
            d = os.path.join(self.tmp, "adir")
            os.makedirs(d, exist_ok=True)
            self.assertPromptUntouched(self.reply("yes", d))
        with self.subTest("only a user line"):
            self.reset()
            path = self.transcript([{"message": {"role": "user", "content": question_text("feature")}}])
            self.assertPromptUntouched(self.reply("yes", path))
        with self.subTest("no transcript_path"):
            self.reset()
            payload = helpers.user_prompt_submit(self.sid, "yes")
            payload.pop("transcript_path")
            self.assertPromptUntouched(self.hook("UserPromptSubmit", payload))
        self.assertEqual(self.status(), "Active specguard mode is simple.")

    def test_MC_12_same_set_rules_as_answer(self):
        with self.subTest("no-approval"):
            self.reset()
            out = self.text_switch("feature no-approval")
            self.assertEqual(out["systemMessage"], "specguard: mode feature (confirmed by you)")
            self.assertEqual(self.status(), "Active specguard mode is feature, approval off.")
        with self.subTest("hard no-approval"):
            self.reset()
            out = self.text_switch("hard no-approval")
            self.assertIn(REFUSAL, out["systemMessage"].splitlines())
            self.assertEqual(self.status(), "Active specguard mode is hard, approval on.")
        with self.subTest("invalid set"):
            self.reset()
            out = self.text_switch("simple+hard")
            self.assertTrue(out["systemMessage"].startswith("specguard: mode not switched,"))
            self.assertIn("is not a valid combination", out["systemMessage"])
            self.assertEqual(self.status(), "Active specguard mode is simple.")

    def test_MC_13_last_assistant_text_shapes(self):
        marker = question_text("feature")
        shapes = {
            "second text block": [self.assistant("Thinking out loud.", marker)],
            "text plus tool_use": [
                {"message": {"role": "assistant", "content": [
                    {"type": "text", "text": marker},
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                ]}}
            ],
            "user line after": [
                self.assistant(marker),
                {"message": {"role": "user", "content": "hmm"}},
            ],
        }
        for name, lines in shapes.items():
            with self.subTest(name):
                self.reset()
                out = self.reply("yes", self.transcript(lines))
                self.assertEqual(out["systemMessage"], "specguard: mode feature (confirmed by you)")


class TestLogging(Base):
    def test_MC_14_answer_switch_logged(self):
        self.answer(question_text("visual+feature"), YES)
        entries = [e for e in self.log() if e["ev"] == "mode-switch"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["via"], "ask")
        self.assertEqual(entries[0]["modes"], ["feature", "visual"])

    def test_MC_14_text_switch_logged(self):
        self.text_switch("feature")
        entries = [e for e in self.log() if e["ev"] == "mode-switch"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["via"], "text")
        self.assertEqual(entries[0]["modes"], ["feature"])

    def test_MC_14_declined_question_logged(self):
        self.answer(question_text("feature"), STAY)
        events = [e for e in self.log() if e["ev"] in ("mode-unchanged", "mode-switch")]
        self.assertEqual([(e["ev"], e["via"]) for e in events], [("mode-unchanged", "ask")])


if __name__ == "__main__":
    unittest.main()
