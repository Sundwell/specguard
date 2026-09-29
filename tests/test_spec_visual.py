import copy
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

MARKER_RE = re.compile(r"specguard-approve visual@(\d+)")

BASE_CONFIG = {
    "version": 1,
    "modes": {"default": "visual"},
    "visual": {
        "ui_paths": ["app/"],
        "deviations_log": "docs/design-deviations.md",
        "notes": ".claude/specguard/visual-notes.md",
    },
}

FILES = {
    "app/button.tsx": "export const B = 1;\n",
    "app/a/b/c.vue": "<template></template>\n",
    "application/x.tsx": "x\n",
    "src/app/x.tsx": "x\n",
    "docs/note.md": "note\n",
}


def config(default="visual", ui_paths=None, drop_visual=False):
    cfg = copy.deepcopy(BASE_CONFIG)
    cfg["modes"]["default"] = default
    if ui_paths is not None:
        cfg["visual"]["ui_paths"] = ui_paths
    if drop_visual:
        del cfg["visual"]
    return cfg


class VisualBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sg-vis-")
        self.state_home = tempfile.mkdtemp(prefix="sg-vis-state-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)
        self.project = None
        self.tcount = 0

    def build(self, cfg=None):
        self.state_home = tempfile.mkdtemp(prefix="sg-vis-state-", dir=self.tmp)
        self.project = helpers.make_project(
            os.path.join(self.tmp, "proj"),
            config=cfg if cfg is not None else config(),
            files=FILES,
            git=True,
            commit=True,
        )

    def hook(self, event, payload):
        code, out, _err = helpers.run_hook(event, payload, self.project, state_home=self.state_home)
        self.assertEqual(code, 0)
        return out

    def edit(self, sid, rel="app/button.tsx", tool="Edit", absolute=True, cwd=None, **extra):
        path = os.path.join(self.project, rel) if absolute else rel
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        if cwd is not None:
            extra["cwd"] = cwd
        payload = helpers.pre_tool_use(sid, tool, {key: path, "old_string": "a", "new_string": "b"}, **extra)
        return self.hook("PreToolUse", payload)

    def reason(self, out):
        self.assertIsNotNone(out)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        return hso["permissionDecisionReason"]

    def marker(self, out):
        m = MARKER_RE.search(self.reason(out))
        self.assertIsNotNone(m)
        return int(m.group(1))

    def question(self, n):
        return "Approve the UI change? specguard-approve visual@{}".format(n)

    def click(self, sid, question, chosen, labels=("✓ Approve", "No")):
        ti = {
            "questions": [{"question": question, "options": [{"label": l} for l in labels]}],
            "answers": {question: chosen},
        }
        return self.hook("PostToolUse", helpers.post_tool_use(sid, "AskUserQuestion", ti, ti))

    def text(self, sid, prompt, assistant_text, **extra):
        if assistant_text is None:
            path = os.path.join(self.tmp, "missing-transcript.jsonl")
        else:
            self.tcount += 1
            path = os.path.join(self.tmp, "t{}.jsonl".format(self.tcount))
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"message": {"role": "assistant",
                                                "content": [{"type": "text", "text": assistant_text}]}}) + "\n")
        return self.hook("UserPromptSubmit", helpers.user_prompt_submit(sid, prompt, transcript_path=path, **extra))

    def switch(self, sid, mode):
        return self.hook("UserPromptExpansion", helpers.user_prompt_expansion(sid, "specguard:mode", mode))

    def is_open(self, out):
        return out is not None and "visual gate open" in (out.get("systemMessage") or "")

    def log(self):
        key = os.path.abspath(self.project).replace("/", "-")
        path = os.path.join(self.state_home, "specguard", key, "log.jsonl")
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def open_by_click(self, sid):
        n = self.marker(self.edit(sid))
        out = self.click(sid, self.question(n), "✓ Approve")
        self.assertTrue(self.is_open(out))
        return n


class TestGateDenials(VisualBase):
    def test_VG_1_write_tools_denied_on_ui_path(self):
        cases = [
            ("Edit", "app/button.tsx"),
            ("Write", "app/new.tsx"),
            ("MultiEdit", "app/button.tsx"),
            ("NotebookEdit", "app/n.ipynb"),
        ]
        for tool, rel in cases:
            with self.subTest(tool=tool):
                self.build()
                out = self.edit("s-" + tool, rel=rel, tool=tool)
                self.assertIn("specguard-approve visual@", self.reason(out))
                shutil.rmtree(self.project)

    def test_VG_2_first_denial_carries_visual_at_1(self):
        self.build()
        self.assertIn("specguard-approve visual@1", self.reason(self.edit("s1")))

    def test_VG_3_path_matching(self):
        cases = [
            ("app/a/b/c.vue", ["app/"], True),
            ("application/x.tsx", ["app/"], False),
            ("src/app/x.tsx", ["app/"], False),
            ("docs/note.md", ["app/"], False),
            ("app/button.tsx", ["app"], True),
            ("app/button.tsx", ["/app/"], True),
            ("app/button.tsx", [], False),
            ("app/button.tsx", [""], False),
            ("application/x.tsx", ["app"], False),
        ]
        for rel, ui, denied in cases:
            with self.subTest(rel=rel, ui_paths=ui):
                self.build(config(ui_paths=ui))
                out = self.edit("s1", rel=rel)
                if denied:
                    self.assertIn("visual@1", self.reason(out))
                else:
                    self.assertIsNone(out)
                shutil.rmtree(self.project)

    def test_VG_3_apps_sibling_is_not_covered(self):
        self.build(config(ui_paths=["app"]))
        os.makedirs(os.path.join(self.project, "apps"))
        self.assertIsNone(self.edit("s1", rel="apps/x.tsx"))

    def test_VG_3_relative_path_resolved_against_cwd(self):
        self.build()
        out = self.edit("s1", rel="app/button.tsx", absolute=False, cwd=self.project)
        self.assertIn("visual@1", self.reason(out))

    def test_VG_3_path_outside_project_not_gated(self):
        self.build()
        outside = tempfile.mkdtemp(prefix="sg-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        payload = helpers.pre_tool_use("s1", "Edit", {"file_path": os.path.join(outside, "app", "button.tsx"),
                                                      "old_string": "a", "new_string": "b"})
        self.assertIsNone(self.hook("PreToolUse", payload))

    def test_VG_4_read_and_bash_pass(self):
        self.build()
        read = helpers.pre_tool_use("s1", "Read", {"file_path": os.path.join(self.project, "app/button.tsx")})
        self.assertIsNone(self.hook("PreToolUse", read))
        bash = helpers.pre_tool_use("s1", "Bash", {"command": "ls app"})
        self.assertIsNone(self.hook("PreToolUse", bash))

    def test_VG_5_non_visual_modes_and_missing_visual_key_not_gated(self):
        cases = [
            ("feature", False),
            ("simple", False),
            ("hard", False),
            ("visual", True),
        ]
        for default, drop in cases:
            with self.subTest(default=default, drop_visual=drop):
                self.build(config(default=default, drop_visual=drop))
                self.assertIsNone(self.edit("s1"))
                shutil.rmtree(self.project)

    def test_VG_5_switch_into_visual_feature_activates_gate(self):
        self.build(config(default="feature"))
        self.assertIsNone(self.edit("s1"))
        self.switch("s1", "visual+feature")
        self.assertIn("visual@", self.reason(self.edit("s1")))

    def test_VG_6_same_marker_while_request_open(self):
        self.build()
        first = self.marker(self.edit("s1"))
        self.assertEqual(first, 1)
        self.assertEqual(self.marker(self.edit("s1")), 1)
        self.assertEqual(self.marker(self.edit("s1", rel="app/a/b/c.vue")), 1)

    def test_VG_7_marker_grows_after_switch(self):
        self.build()
        n = self.open_by_click("s1")
        self.assertEqual(n, 1)
        self.switch("s1", "visual")
        self.assertIn("specguard-approve visual@2", self.reason(self.edit("s1")))


class TestClickApproval(VisualBase):
    def test_VG_8_click_opens_gate_for_all_ui_paths(self):
        self.build()
        self.open_by_click("s1")
        self.assertIsNone(self.edit("s1"))
        self.assertIsNone(self.edit("s1", rel="app/a/b/c.vue"))
        self.assertIsNone(self.edit("s1", rel="app/new.tsx", tool="Write"))

    def test_VG_8_click_on_non_check_label_keeps_closed(self):
        self.build()
        n = self.marker(self.edit("s1"))
        out = self.click("s1", self.question(n), "No")
        self.assertFalse(self.is_open(out))
        self.assertEqual(self.marker(self.edit("s1")), n)

    def test_VG_8_question_without_marker_keeps_closed(self):
        self.build()
        n = self.marker(self.edit("s1"))
        out = self.click("s1", "Approve the UI change?", "✓ Approve")
        self.assertFalse(self.is_open(out))
        self.assertEqual(self.marker(self.edit("s1")), n)

    def test_VG_8_marker_of_other_number_keeps_closed(self):
        self.build()
        self.assertEqual(self.marker(self.edit("s1")), 1)
        out = self.click("s1", self.question(7), "✓ Approve")
        self.assertFalse(self.is_open(out))
        self.assertEqual(self.marker(self.edit("s1")), 1)

    def test_VG_8_options_without_check_keep_closed(self):
        self.build()
        n = self.marker(self.edit("s1"))
        out = self.click("s1", self.question(n), "A", labels=("A", "B"))
        self.assertFalse(self.is_open(out))
        self.assertEqual(self.marker(self.edit("s1")), n)


class TestTextApproval(VisualBase):
    def request(self, sid="s1"):
        n = self.marker(self.edit(sid))
        return "Please confirm specguard-approve visual@{}".format(n)

    def test_VG_9_approval_words_open_gate(self):
        for prompt in ["approve", "апрув", "схвалюю", "ok go"]:
            with self.subTest(prompt=prompt):
                self.build()
                said = self.request()
                out = self.text("s1", prompt, said)
                self.assertTrue(self.is_open(out))
                self.assertIsNone(self.edit("s1"))
                shutil.rmtree(self.project)

    def test_VG_9_rejected_prompts_keep_closed(self):
        for prompt in ["yes", "да", "так", "approve?", "approve please now", "approve the change"]:
            with self.subTest(prompt=prompt):
                self.build()
                said = self.request()
                out = self.text("s1", prompt, said)
                self.assertFalse(self.is_open(out))
                self.assertIn("visual@1", self.reason(self.edit("s1")))
                shutil.rmtree(self.project)

    def test_VG_9_assistant_message_without_marker(self):
        self.build()
        self.request()
        out = self.text("s1", "approve", "Shall I proceed with the change?")
        self.assertFalse(self.is_open(out))
        self.assertIn("visual@1", self.reason(self.edit("s1")))

    def test_VG_9_assistant_message_with_other_number(self):
        self.build()
        self.request()
        out = self.text("s1", "approve", "Please confirm specguard-approve visual@9")
        self.assertFalse(self.is_open(out))
        self.assertIn("visual@1", self.reason(self.edit("s1")))

    def test_VG_9_unreadable_transcript(self):
        self.build()
        self.request()
        out = self.text("s1", "approve", None)
        self.assertFalse(self.is_open(out))
        self.assertIn("visual@1", self.reason(self.edit("s1")))

    def test_VG_9_no_open_request(self):
        self.build()
        out = self.text("s1", "approve", "Please confirm specguard-approve visual@1")
        self.assertFalse(self.is_open(out))
        self.assertIn("visual@1", self.reason(self.edit("s1")))

    def test_VG_9_role_prompt_does_not_open(self):
        self.build()
        said = self.request()
        out = self.text("s1", "approve", said, agent_type="specguard:tester")
        self.assertFalse(self.is_open(out))
        self.assertIn("visual@1", self.reason(self.edit("s1")))


class TestSwitchRolesSessionsLog(VisualBase):
    def test_VG_10_switch_into_visual_closes_open_gate(self):
        self.build()
        self.open_by_click("s1")
        self.switch("s1", "visual")
        self.assertIn("visual@2", self.reason(self.edit("s1")))

    def test_VG_10_switch_away_keeps_gate_and_back_closes_it(self):
        self.build()
        self.open_by_click("s1")
        self.switch("s1", "feature")
        self.assertIsNone(self.edit("s1"))
        self.switch("s1", "visual+feature")
        self.assertIn("visual@", self.reason(self.edit("s1")))

    def test_VG_10_switch_drops_open_request(self):
        self.build()
        self.assertEqual(self.marker(self.edit("s1")), 1)
        self.switch("s1", "visual")
        self.assertEqual(self.marker(self.edit("s1")), 2)

    def test_VG_11_tester_edit_not_denied(self):
        self.build()
        self.assertIsNone(self.edit("s1", agent_type="specguard:tester"))

    def test_VG_11_devils_advocate_denied_by_own_rule(self):
        self.build()
        out = self.edit("s1", agent_type="specguard:devils-advocate")
        self.assertNotIn("specguard-approve visual", self.reason(out))

    def test_VG_12_go_in_one_session_does_not_open_another(self):
        self.build()
        self.open_by_click("s1")
        self.assertIsNone(self.edit("s1"))
        self.assertIn("specguard-approve visual@1", self.reason(self.edit("s2")))

    def test_VG_13_click_logs_deny_then_approval_ask(self):
        self.build()
        self.open_by_click("s1")
        rows = [r for r in self.log() if r.get("rule") == "V"]
        self.assertEqual([r["ev"] for r in rows], ["deny", "approval"])
        self.assertEqual(rows[1]["via"], "ask")

    def test_VG_13_text_logs_approval_text(self):
        self.build()
        n = self.marker(self.edit("s1"))
        out = self.text("s1", "approve", "Confirm specguard-approve visual@{}".format(n))
        self.assertTrue(self.is_open(out))
        rows = [r for r in self.log() if r.get("rule") == "V" and r.get("ev") == "approval"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["via"], "text")

    def test_VG_13_switch_logs_visual_close(self):
        self.build()
        self.open_by_click("s1")
        self.switch("s1", "visual")
        rows = [r for r in self.log() if r.get("ev") == "visual-close"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule"], "V")


if __name__ == "__main__":
    unittest.main()
