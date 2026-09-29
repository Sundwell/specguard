import json
import os
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

# ---------------------------------------------------------------------------
# Table-driven grammar tests - plan.md 2.3.1, plus analogous en/uk rows.
# Each entry is (message, expected_modes_or_None, expected_approval_or_None).
# expected_modes is a frozenset for a switch, "Q" for the trailing-? notice,
# "COMBO" for a structurally valid but unsupported combination, or None for
# a silent no-op.
# ---------------------------------------------------------------------------

RU_ROWS = [
    ("го фичспек", frozenset(["feature"]), True),
    ("Го, фичспек.", frozenset(["feature"]), True),
    ("режим фичспек", frozenset(["feature"]), True),
    ("го фич спек сделай доменную часть", frozenset(["feature"]), True),
    ("Го фич-спек, сделай доменную часть.", frozenset(["feature"]), True),
    ("го фичспек без апрува.", frozenset(["feature"]), False),
    ("го фичспек без апрува, сделай X", frozenset(["feature"]), False),
    ("Го фичспек, без апрува.", frozenset(["feature"]), False),
    ("го фичспек без апрува тестера не запускай", frozenset(["feature"]), True),
    ("Го фичспек, только без апрува тестера не запускай", frozenset(["feature"]), True),
    ("го хардмод", frozenset(["hard"]), True),
    ("режим хард мод", frozenset(["hard"]), True),
    ("го хардмод режим", frozenset(["hard"]), True),
    ("го хард-мод", frozenset(["hard"]), True),
    ("го хардмод без апрува", frozenset(["hard"]), True),  # refused, stays on
    ("го визуал и фичспек", frozenset(["visual", "feature"]), True),
    ("Го визуал, фичспек.", frozenset(["visual", "feature"]), True),
    ("го визуал + хардмод", frozenset(["visual", "hard"]), True),
    ("го простой", frozenset(["simple"]), None),
    ("Го простой.", frozenset(["simple"]), None),
    ("го простой режим", frozenset(["simple"]), None),
    ("Го простой — поправь футер.", frozenset(["simple"]), None),
    ("го простой и фичспек", "COMBO", None),
    ("го хардмод?", "Q", None),
    ("Го фичспек. Что скажешь?", "Q", None),
    ("Го фичспек, сделай доменную часть?", "Q", None),
    ("го простой фикс", None, None),
    ("Го, там простой фикс", None, None),
    ("Простой режим тут не подойдёт", None, None),
    ("Что будет в режиме простой?", None, None),
    ("Выключи режим визуал", None, None),
    ("Режим визуал выключи", None, None),
    ("Как работает хардмод режим?", None, None),
    ("Почему тестер ушёл без апрува?", None, None),
    ("В прошлый раз без апрува вышло плохо", None, None),
    ("Это не простой режим, а фичспек", None, None),
    ("почему тестер запущен без апрува", None, None),
    ("почему ты в простой режим перешёл", None, None),
    ("простой режим", None, None),
    ("не го простой", None, None),
]

EN_ROWS = [
    ("go feature spec", frozenset(["feature"]), True),
    ("Go, feature.", frozenset(["feature"]), True),
    ("go feature spec do the domain part", frozenset(["feature"]), True),
    ("go feature spec, do the domain part.", frozenset(["feature"]), True),
    ("go feature spec no approval.", frozenset(["feature"]), False),
    ("go feature spec no approval, do X", frozenset(["feature"]), False),
    ("Go feature spec, no approval.", frozenset(["feature"]), False),
    ("go hard mode", frozenset(["hard"]), True),
    ("go hard mode no approval", frozenset(["hard"]), True),  # refused
    ("go visual and feature", frozenset(["visual", "feature"]), True),
    ("Go visual, feature.", frozenset(["visual", "feature"]), True),
    ("go visual + hard mode", frozenset(["visual", "hard"]), True),
    ("go simple", frozenset(["simple"]), None),
    ("Go simple.", frozenset(["simple"]), None),
    ("go simple and feature", "COMBO", None),
    ("go hard mode?", "Q", None),
    ("Go feature spec. What do you say?", "Q", None),
    ("go simple fix", None, None),
    ("Simple mode won't do here", None, None),
    ("What happens in simple mode?", None, None),
    ("Turn off visual mode", None, None),
    ("Why did the tester run without approval?", None, None),
    ("simple mode", None, None),
    ("not go simple", None, None),
]

UK_ROWS = [
    ("го фічспек", frozenset(["feature"]), True),
    ("Го, фічспек.", frozenset(["feature"]), True),
    ("режим фічспек", frozenset(["feature"]), True),
    ("го фіч спек зроби доменну частину", frozenset(["feature"]), True),
    ("Го фіч-спек, зроби доменну частину.", frozenset(["feature"]), True),
    ("го фічспек без апруву.", frozenset(["feature"]), False),
    ("го фічспек без апруву, зроби X", frozenset(["feature"]), False),
    ("Го фічспек, без апруву.", frozenset(["feature"]), False),
    ("го хардмод", frozenset(["hard"]), True),
    ("режим хард мод", frozenset(["hard"]), True),
    ("го хардмод режим", frozenset(["hard"]), True),
    ("го хард-мод", frozenset(["hard"]), True),
    ("го хардмод без апруву", frozenset(["hard"]), True),  # refused
    ("го візуал і фічспек", frozenset(["visual", "feature"]), True),
    ("Го візуал, фічспек.", frozenset(["visual", "feature"]), True),
    ("го візуал + хардмод", frozenset(["visual", "hard"]), True),
    ("го простий", frozenset(["simple"]), None),
    ("Го простий.", frozenset(["simple"]), None),
    ("го простий і фічспек", "COMBO", None),
    ("го хардмод?", "Q", None),
    ("Го фічспек. Що скажеш?", "Q", None),
    ("го простий фікс", None, None),
    ("Простий режим тут не підійде", None, None),
    ("Як працює хардмод режим?", None, None),
    ("не го простий", None, None),
]

ALL_ROWS = [("ru", r) for r in RU_ROWS] + [("en", r) for r in EN_ROWS] + [("uk", r) for r in UK_ROWS]


def _make_project(tmp):
    return helpers.make_project(
        tmp,
        config={"version": 1, "modes": {"default": "simple", "approval_default": True}},
    )


class ModeTableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-modes-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project_dir = _make_project(self.tmp)

    def _run(self, prompt, session_id="s1"):
        payload = helpers.user_prompt_submit(session_id, prompt)
        return helpers.run_hook(
            "UserPromptSubmit", payload, self.project_dir, state_home=self.state_home
        )

    def test_table_rows(self):
        self.assertGreaterEqual(len(ALL_ROWS), 60)
        for idx, (lang, (message, expected, expected_approval)) in enumerate(ALL_ROWS):
            session_id = "row-{}-{}".format(lang, idx)
            with self.subTest(lang=lang, message=message):
                code, out, err = self._run(message, session_id=session_id)
                self.assertEqual(code, 0, err)
                session_file = os.path.join(
                    self.state_home, "specguard",
                    os.path.abspath(self.project_dir).replace(os.sep, "-"),
                    "sessions", "{}.json".format(session_id),
                )
                if os.path.isfile(session_file):
                    with open(session_file) as f:
                        session = json.load(f)
                else:
                    session = {}

                if expected == "Q":
                    self.assertTrue(out and "systemMessage" in out)
                    self.assertIn('"?"', out["systemMessage"])
                    self.assertNotIn("modes", session)
                elif expected == "COMBO":
                    self.assertTrue(out and "systemMessage" in out)
                    self.assertIn("accepted", out["systemMessage"])
                    self.assertNotIn("modes", session)
                elif expected is None:
                    if out is not None:
                        self.assertNotIn("systemMessage", out)
                    self.assertNotIn("modes", session)
                else:
                    self.assertEqual(set(session.get("modes", [])), set(expected))
                    if expected_approval is not None:
                        self.assertEqual(session.get("approval"), expected_approval)
                    self.assertTrue(out and "systemMessage" in out)
                    self.assertTrue(out["systemMessage"].startswith("specguard:"))


class ModeCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-modes-cmd-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-state-cmd-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project_dir = _make_project(self.tmp)

    def _run_command(self, args, session_id="c1"):
        payload = helpers.user_prompt_expansion(session_id, "specguard:mode", args)
        return helpers.run_hook(
            "UserPromptExpansion", payload, self.project_dir, state_home=self.state_home
        )

    def _session(self, session_id):
        session_file = os.path.join(
            self.state_home, "specguard",
            os.path.abspath(self.project_dir).replace(os.sep, "-"),
            "sessions", "{}.json".format(session_id),
        )
        if not os.path.isfile(session_file):
            return {}
        with open(session_file) as f:
            return json.load(f)

    def test_accepted_forms(self):
        for args in ("simple", "feature", "hard", "visual", "visual+feature", "visual+hard"):
            with self.subTest(args=args):
                sid = "acc-" + args
                code, out, err = self._run_command(args, session_id=sid)
                self.assertEqual(code, 0, err)
                self.assertTrue(out and "systemMessage" in out)
                session = self._session(sid)
                self.assertEqual(set(session["modes"]), set(args.split("+")))

    def test_refused_pairs(self):
        for args in ("simple+feature", "feature+hard", "simple+hard", "simple+visual+feature", "bogus"):
            with self.subTest(args=args):
                sid = "ref-" + args
                code, out, err = self._run_command(args, session_id=sid)
                self.assertEqual(code, 0, err)
                self.assertTrue(out and "systemMessage" in out)
                self.assertIn("not switched", out["systemMessage"])
                self.assertEqual(self._session(sid), {})

    def test_no_approval_in_hard_refused(self):
        sid = "hard-noapp"
        code, out, err = self._run_command("hard no-approval", session_id=sid)
        self.assertEqual(code, 0, err)
        self.assertIn("refused", out["systemMessage"])
        session = self._session(sid)
        self.assertEqual(session["modes"], ["hard"])
        self.assertTrue(session["approval"])

    def test_no_approval_in_feature_applies(self):
        sid = "feature-noapp"
        code, out, err = self._run_command("feature no-approval", session_id=sid)
        self.assertEqual(code, 0, err)
        session = self._session(sid)
        self.assertEqual(session["modes"], ["feature"])
        self.assertFalse(session["approval"])

    def test_model_cannot_switch_mode_by_bare_skill_tool(self):
        # disable-model-invocation is enforced by the harness before any hook
        # fires (Phase 0 C3); nothing in modes.py can be asked to simulate a
        # Skill-tool call succeeding, so this test only asserts the skill
        # frontmatter carries the flag the harness checks.
        skill_path = os.path.join(_REPO_ROOT, "skills", "mode", "SKILL.md")
        with open(skill_path) as f:
            content = f.read()
        self.assertIn("disable-model-invocation: true", content)


class IgnoredPromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specguard-modes-ignore-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-state-ignore-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project_dir = _make_project(self.tmp)

    def test_role_session_prompt_ignored(self):
        payload = helpers.user_prompt_submit("s1", "го хардмод", agent_type="specguard:tester")
        code, out, err = helpers.run_hook(
            "UserPromptSubmit", payload, self.project_dir, state_home=self.state_home
        )
        self.assertEqual(code, 0, err)
        self.assertIsNone(out)

    def test_task_notification_ignored(self):
        payload = helpers.user_prompt_submit(
            "s1", "<task-notification>\n<task-id>1</task-id>\ngo hard mode\n</task-notification>"
        )
        code, out, err = helpers.run_hook(
            "UserPromptSubmit", payload, self.project_dir, state_home=self.state_home
        )
        self.assertEqual(code, 0, err)
        # a task-notification prompt never switches a mode or records an
        # approval (its first character is not a letter); the routine
        # one-line status context may still be present.
        if out is not None:
            self.assertNotIn("systemMessage", out)
        session_file = os.path.join(
            self.state_home, "specguard",
            os.path.abspath(self.project_dir).replace(os.sep, "-"),
            "sessions", "s1.json",
        )
        self.assertFalse(os.path.isfile(session_file))


if __name__ == "__main__":
    unittest.main()
