import contextlib
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

from specguard import config, context, core  # noqa: E402
from specguard.state import State  # noqa: E402


@contextlib.contextmanager
def _state_home(path):
    old = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = path
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = old


def _make_ctx(project_dir, state_home, event, data, raw_config=None):
    raw_config = raw_config if raw_config is not None else {"version": 1}
    cfg = config.Config(raw_config)
    with _state_home(state_home):
        state = State(project_dir)
        return core.Ctx(event, data, cfg, project_dir, state)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="specguard-context-")
        self.addCleanup(shutil.rmtree, self.project_dir, ignore_errors=True)
        self.state_home = tempfile.mkdtemp(prefix="specguard-state-")
        self.addCleanup(shutil.rmtree, self.state_home, ignore_errors=True)

    def _write_notes(self, rel_path, content):
        full = os.path.join(self.project_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def test_role_notes_included_under_cap(self):
        self._write_notes(".claude/specguard/tester-notes.md", "Read the spec first.\nNever call tests пробы.")
        ctx = _make_ctx(
            self.project_dir,
            self.state_home,
            "SubagentStart",
            {"session_id": "s1", "agent_id": "a1", "agent_type": "specguard:tester"},
        )
        out = context.on_subagent_start(ctx)
        self.assertIsNotNone(out)
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Never call tests", text)
        self.assertIn("Project root:", text)
        self.assertIn("Allowed grep roots:", text)

    def test_cap_drops_notes_over_9000_chars(self):
        long_notes = "x" * 9500
        self._write_notes(".claude/specguard/tester-notes.md", long_notes)
        ctx = _make_ctx(
            self.project_dir,
            self.state_home,
            "SubagentStart",
            {"session_id": "s2", "agent_id": "a2", "agent_type": "specguard:tester"},
        )
        out = context.on_subagent_start(ctx)
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("x" * 100, text)
        self.assertIn("read", text.lower())
        self.assertIn("first", text.lower())

    def test_one_line_for_other_subagents(self):
        ctx = _make_ctx(
            self.project_dir,
            self.state_home,
            "SubagentStart",
            {"session_id": "s3", "agent_id": "a3", "agent_type": "general-purpose"},
        )
        out = context.on_subagent_start(ctx)
        self.assertIsNotNone(out)
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn("Active specguard mode is", text)
        self.assertIn("Only the main session can ask the user", text)

    def test_mode_rules_non_empty_for_every_accepted_set(self):
        self.assertEqual(context.mode_rules(["simple"], True), "")
        for modes in (
            ["feature"],
            ["hard"],
            ["visual"],
            ["visual", "feature"],
            ["visual", "hard"],
        ):
            with self.subTest(modes=modes):
                text = context.mode_rules(modes, True)
                self.assertTrue(text)

        self.assertIn("Talk to the user in the user's language.", context.mode_rules(["feature"], True))
        self.assertIn("mutation check", context.mode_rules(["hard"], True))
        self.assertIn("DESIGN | NOW", context.mode_rules(["visual"], True))
        self.assertIn("DESIGN | NOW", context.mode_rules(["visual", "hard"], True))


if __name__ == "__main__":
    unittest.main()
