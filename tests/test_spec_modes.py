import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402

ALL_SETS = ("simple", "feature", "hard", "visual", "visual+feature", "visual+hard")
STATUS_RE = re.compile(r"Active specguard mode is [^\n]*\.")
PRIMED = "Active specguard mode is feature, approval off."


class Env:
    def __init__(self, case, config=None):
        self.case = case
        self.project_dir = tempfile.mkdtemp(prefix="specguard-spec-modes-")
        self.state_home = tempfile.mkdtemp(prefix="specguard-spec-modes-state-")
        case.addCleanup(shutil.rmtree, self.project_dir, ignore_errors=True)
        case.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        if config is None:
            config = {"version": 1, "modes": {"default": "simple", "approval_default": True}}
        helpers.make_project(self.project_dir, config=config)

    def phrase(self, prompt, sid="s1", **extra):
        payload = helpers.user_prompt_submit(sid, prompt, **extra)
        code, out, _ = helpers.run_hook("UserPromptSubmit", payload, self.project_dir,
                                        state_home=self.state_home)
        self.case.assertEqual(code, 0)
        return out

    def command(self, args, sid="s1", name="specguard:mode", **extra):
        payload = helpers.user_prompt_expansion(sid, name, args, **extra)
        code, out, _ = helpers.run_hook("UserPromptExpansion", payload, self.project_dir,
                                        state_home=self.state_home)
        self.case.assertEqual(code, 0)
        return out

    def pre(self, tool, tool_input, sid="s1"):
        payload = helpers.pre_tool_use(sid, tool, tool_input)
        code, out, _ = helpers.run_hook("PreToolUse", payload, self.project_dir,
                                        state_home=self.state_home)
        self.case.assertEqual(code, 0)
        return out

    def status(self, sid="s1"):
        ctx = context(self.phrase("hello", sid))
        found = STATUS_RE.findall(ctx)
        self.case.assertTrue(found, ctx)
        self.case.assertTrue(ctx.rstrip().endswith(found[-1]), ctx)
        return found[-1]

    def prime(self, sid="s1"):
        out = self.command("feature no-approval", sid)
        self.case.assertEqual(out["systemMessage"], "specguard: mode feature, approval off")
        self.case.assertEqual(self.status(sid), PRIMED)

    def log(self):
        name = self.project_dir.replace("/", "-")
        path = os.path.join(self.state_home, "specguard", name, "log.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def switches(self):
        return [e for e in self.log() if e.get("ev") == "mode-switch"]


def context(out):
    if not out:
        return ""
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


def message(out):
    if not out:
        return None
    return out.get("systemMessage")


def line(mode, approval=None):
    if approval is None:
        return "Active specguard mode is {}.".format(mode)
    return "Active specguard mode is {}, approval {}.".format(mode, approval)


class ModesSpec(unittest.TestCase):
    def assert_phrase(self, prompt, expected):
        env = Env(self)
        env.phrase(prompt)
        self.assertEqual(env.status(), expected, prompt)

    def assert_phrase_unchanged(self, prompt):
        env = Env(self)
        env.prime()
        out = env.phrase(prompt)
        self.assertEqual(env.status(), PRIMED, prompt)
        return out

    def assert_command_refused(self, args):
        env = Env(self)
        env.prime()
        out = env.command(args)
        self.assertTrue(message(out).startswith("specguard: mode not switched, usage /specguard:mode"),
                        args)
        self.assertEqual(env.status(), PRIMED, args)
        return out

    # MO-1

    def test_MO_1_command_sets_mode_message_and_status_line(self):
        rows = [
            ("feature", "specguard: mode feature, approval on", line("feature", "on")),
            ("simple", "specguard: mode simple, approval on", line("simple")),
            ("visual", "specguard: mode visual, approval on", line("visual")),
            ("visual+feature", "specguard: mode visual+feature, approval on",
             line("visual+feature", "on")),
            ("visual+hard", "specguard: mode visual+hard, approval on", line("visual+hard", "on")),
            ("hard", "specguard: mode hard, approval on", line("hard", "on")),
        ]
        for args, msg, status in rows:
            with self.subTest(args=args):
                env = Env(self)
                out = env.command(args)
                self.assertEqual(out["systemMessage"], msg)
                self.assertEqual(env.status(), status)

    # MO-2

    def test_MO_2_approval_default_false_applies_except_in_hard(self):
        rows = [
            ("feature", "specguard: mode feature, approval off", line("feature", "off")),
            ("visual+feature", "specguard: mode visual+feature, approval off",
             line("visual+feature", "off")),
            ("hard", "specguard: mode hard, approval on", line("hard", "on")),
            ("visual+hard", "specguard: mode visual+hard, approval on", line("visual+hard", "on")),
        ]
        for args, msg, status in rows:
            with self.subTest(args=args):
                env = Env(self, {"version": 1, "modes": {"default": "simple", "approval_default": False}})
                out = env.command(args)
                self.assertEqual(out["systemMessage"], msg)
                self.assertEqual(env.status(), status)

    def test_MO_2_missing_approval_default_key_means_on(self):
        env = Env(self, {"version": 1, "modes": {"default": "simple"}})
        out = env.command("feature")
        self.assertEqual(out["systemMessage"], "specguard: mode feature, approval on")
        self.assertEqual(env.status(), line("feature", "on"))

    def test_MO_2_no_approval_turns_approval_off(self):
        rows = [
            ("feature no-approval", line("feature", "off")),
            ("visual+feature no-approval", line("visual+feature", "off")),
        ]
        for args, status in rows:
            with self.subTest(args=args):
                env = Env(self)
                out = env.command(args)
                self.assertEqual(out["systemMessage"],
                                 "specguard: mode {}, approval off".format(args.split()[0]))
                self.assertEqual(env.status(), status)

    def test_MO_2_switch_does_not_carry_approval_over(self):
        env = Env(self)
        env.prime()
        env.command("feature")
        self.assertEqual(env.status(), line("feature", "on"))

    # MO-3

    def test_MO_3_no_approval_with_hard_is_refused_but_switch_happens(self):
        for args in ("hard no-approval", "visual+hard no-approval"):
            with self.subTest(args=args):
                env = Env(self)
                out = env.command(args)
                mode = args.split()[0]
                self.assertEqual(out["systemMessage"].split("\n"), [
                    "specguard: mode {}, approval on".format(mode),
                    "specguard: no-approval refused, hard mode always requires approval",
                ])
                self.assertEqual(env.status(), line(mode, "on"))

    # MO-4

    def test_MO_4_malformed_command_changes_nothing_and_lists_usage(self):
        rows = ["", "turbo", "simple+feature", "feature+hard", "hard+feature",
                "feature yes-approval", "feature no-approval now"]
        for args in rows:
            with self.subTest(args=args):
                out = self.assert_command_refused(args)
                for accepted in ALL_SETS:
                    self.assertIn(accepted, message(out))

    # MO-5

    def test_MO_5_other_command_name_prints_nothing_and_changes_nothing(self):
        env = Env(self)
        env.prime()
        out = env.command("hard", name="specguard:guide")
        self.assertIsNone(out)
        self.assertEqual(env.status(), PRIMED)

    def test_MO_5_role_session_command_prints_nothing_and_changes_nothing(self):
        for role in ("specguard:tester", "specguard:devils-advocate"):
            with self.subTest(role=role):
                env = Env(self)
                env.prime()
                out = env.command("hard", agent_type=role)
                self.assertIsNone(out)
                self.assertEqual(env.status(), PRIMED)

    # MO-6

    def test_MO_6_phrase_switches_with_default_approval(self):
        rows = [
            ("go feature spec", line("feature", "on")),
            ("го фичспек", line("feature", "on")),
            ("го фічспек", line("feature", "on")),
            ("режим фичспек", line("feature", "on")),
            ("go hard mode", line("hard", "on")),
            ("go hardmode", line("hard", "on")),
            ("го хард мод", line("hard", "on")),
            ("го хардмод", line("hard", "on")),
            ("go visual", line("visual")),
            ("го візуал", line("visual")),
            ("go simple", line("simple")),
            ("го простий", line("simple")),
            ("го простой", line("simple")),
            ("го визуал и фичспек", line("visual+feature", "on")),
            ("го візуал і фічспек", line("visual+feature", "on")),
            ("go visual and feature spec", line("visual+feature", "on")),
            ("го визуал + хардмод", line("visual+hard", "on")),
            ("Го визуал, фичспек.", line("visual+feature", "on")),
            ("go visual і фічспек", line("visual+feature", "on")),
            ("го хардмод режим", line("hard", "on")),
            ("го простой режим", line("simple")),
        ]
        for prompt, status in rows:
            with self.subTest(prompt=prompt):
                self.assert_phrase(prompt, status)

    def test_MO_6_phrase_opt_out_turns_approval_off(self):
        rows = [
            ("го фичспек без апруву", line("feature", "off")),
            ("go feature spec no approval", line("feature", "off")),
        ]
        for prompt, status in rows:
            with self.subTest(prompt=prompt):
                self.assert_phrase(prompt, status)

    def test_MO_6_phrase_output_matches_command_output(self):
        env = Env(self)
        out = env.phrase("го фичспек без апруву")
        self.assertEqual(out["systemMessage"], "specguard: mode feature, approval off")

    def test_MO_6_hard_phrase_with_opt_out_is_refused_like_the_command(self):
        env = Env(self)
        out = env.phrase("go hard mode no approval.")
        self.assertEqual(out["systemMessage"].split("\n"), [
            "specguard: mode hard, approval on",
            "specguard: no-approval refused, hard mode always requires approval",
        ])
        self.assertEqual(env.status(), line("hard", "on"))

    # MO-7

    def test_MO_7_case_dashes_and_punctuation_do_not_matter(self):
        rows = [
            ("ГО ФИЧСПЕК", line("feature", "on")),
            ("Го фич-спек, сделай доменную часть.", line("feature", "on")),
            ("го хард-мод", line("hard", "on")),
            ("го фич–спек", line("feature", "on")),
            ("го фич‐спек", line("feature", "on")),
            ("го фич‑спек", line("feature", "on")),
            ("го фич—спек", line("feature", "on")),
            ("Го, фичспек.", line("feature", "on")),
            ("  go feature spec", line("feature", "on")),
            ("\n\t go feature spec", line("feature", "on")),
            ("Go, hard mode!", line("hard", "on")),
            ("Го хардмод; ", line("hard", "on")),
            ("Go feature-spec", line("feature", "on")),
        ]
        for prompt, status in rows:
            with self.subTest(prompt=prompt):
                self.assert_phrase(prompt, status)

    def test_MO_7_colon_after_the_trigger_is_not_a_separator(self):
        out = self.assert_phrase_unchanged("Го: хардмод; ")
        self.assertIsNone(message(out))

    def test_MO_7_yo_and_ye_are_the_same_letter(self):
        self.assert_phrase("го фичё спек", line("feature", "on"))

    # MO-8

    def test_MO_8_phrase_end_rules(self):
        rows = [
            ("го фичспек сделай доменную часть", line("feature", "on")),
            ("go visual and start with the header", line("visual")),
            ("Го простой - поправь футер.", line("simple")),
            ("Го простой. Поправь футер", line("simple")),
            ("go simple, fix the footer", line("simple")),
            ("Го простой\nпоправь футер", line("simple")),
            ("го простой фикс", PRIMED),
            ("Го, там простой фикс", PRIMED),
            ("режим визуал выключи", PRIMED),
            ("go simple fix the footer", PRIMED),
        ]
        for prompt, status in rows:
            with self.subTest(prompt=prompt):
                env = Env(self)
                env.prime()
                env.phrase(prompt)
                self.assertEqual(env.status(), status, prompt)

    # MO-9

    def test_MO_9_opt_out_counts_only_at_the_end_of_the_phrase(self):
        rows = [
            ("го фичспек без апрува.", line("feature", "off")),
            ("Го фичспек, без апрува.", line("feature", "off")),
            ("go feature spec, no approval", line("feature", "off")),
            ("го фичспек без апрува, сделай X", line("feature", "off")),
            ("го фичспек без апрува тестера не запускай", line("feature", "on")),
            ("Го фичспек, только без апрува тестера не запускай", line("feature", "on")),
            ("go feature spec no approval for the tester", line("feature", "on")),
        ]
        for prompt, status in rows:
            with self.subTest(prompt=prompt):
                self.assert_phrase(prompt, status)

    # MO-10

    def test_MO_10_message_not_starting_with_a_phrase_changes_nothing(self):
        rows = ["Простой режим тут не подойдёт", "Что будет в режиме простой?", "не го простой",
                "Как работает хардмод режим?", "Почему тестер ушёл без апрува?",
                "please go feature spec", "Ок. Го фичспек", "simple"]
        for prompt in rows:
            with self.subTest(prompt=prompt):
                out = self.assert_phrase_unchanged(prompt)
                self.assertIsNone(message(out))

    # MO-11

    def test_MO_11_switching_message_ending_with_question_mark_is_refused(self):
        rows = ["го хардмод?", "Го фичспек. Что скажешь?", "Го фичспек, сделай доменную часть?",
                "go simple?"]
        for prompt in rows:
            with self.subTest(prompt=prompt):
                out = self.assert_phrase_unchanged(prompt)
                self.assertEqual(message(out), 'specguard: mode not switched, message ends with "?"')

    def test_MO_11_non_switching_question_prints_no_message(self):
        env = Env(self)
        out = env.phrase("Как ты?")
        self.assertIsNone(message(out))

    # MO-12

    def test_MO_12_invalid_pair_is_explained(self):
        for prompt in ("го простой и фичспек", "go feature and hard mode"):
            with self.subTest(prompt=prompt):
                out = self.assert_phrase_unchanged(prompt)
                msg = message(out)
                self.assertTrue(msg.startswith("specguard: mode not switched,"), msg)
                self.assertIn("is not a valid combination", msg)
                for accepted in ALL_SETS:
                    self.assertIn(accepted, msg)

    # MO-13

    def test_MO_13_first_character_not_a_letter_switches_nothing(self):
        rows = ["<task-notification>го хардмод</task-notification>", "/specguard:mode feature",
                "1. go feature spec", '"го хардмод"']
        for prompt in rows:
            with self.subTest(prompt=prompt):
                self.assert_phrase_unchanged(prompt)

    def test_MO_13_role_session_prompt_prints_nothing_and_switches_nothing(self):
        rows = [("го хардмод", "specguard:tester"), ("go simple", "specguard:devils-advocate")]
        for prompt, role in rows:
            with self.subTest(prompt=prompt, role=role):
                env = Env(self)
                env.prime()
                out = env.phrase(prompt, agent_type=role)
                self.assertIsNone(out)
                self.assertEqual(env.status(), PRIMED)

    # MO-14

    def test_MO_14_switch_replaces_the_whole_previous_set(self):
        rows = [
            (("го хардмод", "го простой"), line("simple")),
            (("го визуал и фичспек", "го фичспек"), line("feature", "on")),
        ]
        for prompts, status in rows:
            with self.subTest(prompts=prompts):
                env = Env(self)
                for p in prompts:
                    env.phrase(p)
                self.assertEqual(env.status(), status)

    def test_MO_14_other_session_is_unaffected(self):
        env = Env(self)
        env.phrase("го хардмод", sid="s1")
        self.assertEqual(env.status("s1"), line("hard", "on"))
        self.assertEqual(env.status("s2"), line("simple"))

    # MO-15

    def test_MO_15_switch_context_carries_intro_and_mode_rules(self):
        rows = [
            ("го хардмод", ["Hard mode adds.", "Feature mode rules.", "specguard:guide"], []),
            ("го фичспек", ["Feature mode rules.", "specguard:guide"], ["Hard mode adds."]),
            ("го визуал", ["Visual mode rules.", "specguard:guide"], ["Feature mode rules."]),
            ("го простой", ["specguard:guide"], ["mode rules."]),
            ("го визуал и фичспек", ["Visual mode rules.", "Feature mode rules."], []),
        ]
        for prompt, present, absent in rows:
            with self.subTest(prompt=prompt):
                env = Env(self)
                ctx = context(env.phrase(prompt))
                for text in present:
                    self.assertIn(text, ctx)
                for text in absent:
                    self.assertNotIn(text, ctx)

    def test_MO_15_prompt_that_does_not_switch_carries_only_the_status_line(self):
        env = Env(self)
        ctx = context(env.phrase("hello"))
        self.assertEqual(ctx.strip(), line("simple"))

    # MO-16

    def test_MO_16_phrase_switch_is_logged(self):
        env = Env(self)
        env.phrase("го фичспек")
        entries = env.switches()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["via"], "phrase")
        self.assertEqual(entries[0]["modes"], ["feature"])

    def test_MO_16_command_switch_is_logged_with_sorted_modes(self):
        env = Env(self)
        env.command("visual+feature")
        entries = env.switches()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["via"], "command")
        self.assertEqual(entries[0]["modes"], ["feature", "visual"])

    def test_MO_16_refusals_write_no_mode_switch_line(self):
        env = Env(self)
        env.command("turbo")
        env.phrase("го хардмод?")
        env.phrase("го простой и фичспек")
        self.assertEqual(env.switches(), [])

    # MO-17

    def assert_deny(self, out):
        self.assertIsNotNone(out)
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        self.assertEqual(spec["permissionDecision"], "deny")
        self.assertTrue(spec["permissionDecisionReason"].startswith("specguard: "))

    def test_MO_17_agent_sent_prompt_with_mode_phrase_is_denied(self):
        rows = [
            ("SendMessage", {"message": "го хардмод"}),
            ("CronCreate", {"prompt": "go feature spec"}),
            ("ScheduleWakeup", {"prompt": "го визуал и фичспек"}),
            ("SendMessage", {"message": "го фичспек сделай X"}),
        ]
        for tool, tool_input in rows:
            with self.subTest(tool=tool, tool_input=tool_input):
                env = Env(self)
                self.assert_deny(env.pre(tool, tool_input))

    def test_MO_17_ordinary_agent_text_passes(self):
        env = Env(self)
        self.assertIsNone(env.pre("SendMessage", {"message": "please run the tests"}))


if __name__ == "__main__":
    unittest.main()
