import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402

ROLES = {
    "version": 1,
    "repo": ".",
    "modes": {"default": "feature", "approval_default": False},
    "hidden": {"segments": ["src"], "names": ["secrets.env"], "paths": ["lib/core"]},
    "tester_readable": ["src/db/schema.ts"],
    "tests": {"segments": ["test"], "paths": ["qa/suites"], "file_regex": "\\.spec\\.ts$"},
    "docs_dirs": ["docs"],
    "specs_dir": "docs/specs",
    "reports": {"advocate_hidden": [".specguard/reports/tester"]},
}


def make_config(name):
    cfg = copy.deepcopy(ROLES)
    if name == "ROLES":
        return cfg
    if name.startswith("NESTED"):
        cfg["repo"] = "app"
        cfg["hidden"] = {"segments": ["src"]}
        cfg["tests"] = {"segments": ["test"]}
        del cfg["tester_readable"]
        del cfg["reports"]
        return cfg
    if name.startswith("MODE_"):
        mode = name[len("MODE_"):]
        cfg["modes"]["default"] = mode if mode in ("simple", "feature", "hard") else "simple"
        return cfg
    raise ValueError(name)


ROLE_TYPES = {
    "executor": None,
    "tester": "tester",
    "specguard:tester": "specguard:tester",
    "advocate": "devils-advocate",
    "specguard:devils-advocate": "specguard:devils-advocate",
    "oh-my-claudecode:executor": "oh-my-claudecode:executor",
}

ALLOW = "allow"

VISUAL_PHRASES = {
    "MODE_visual": "go visual",
    "MODE_visual+feature": "go visual and feature spec",
    "MODE_visual+hard": "go visual and hard mode",
}


class RolesBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sg-roles-")
        self.state_home = tempfile.mkdtemp(prefix="sg-roles-state-")
        self.outside = tempfile.mkdtemp(prefix="sg-roles-out-")
        for d in (self.tmp, self.state_home, self.outside):
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.project = os.path.join(self.tmp, "proj")

    def subst(self, value):
        return (
            value.replace("<outside>", self.outside)
            .replace("<project>", self.project)
            .replace("<parent>", os.path.dirname(self.project))
        )

    def state_dir(self):
        key = os.path.abspath(self.project).replace("/", "-")
        return os.path.join(self.state_home, "specguard", key)

    def last_log(self):
        with open(os.path.join(self.state_dir(), "log.jsonl"), encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        return json.loads(lines[-1])

    def call(self, cfg_name, role, tool, tool_input, agent_id=None):
        helpers.make_project(self.project, config=make_config(cfg_name))
        if cfg_name in VISUAL_PHRASES:
            code, _out, _err = helpers.run_hook(
                "UserPromptSubmit",
                helpers.user_prompt_submit("s1", VISUAL_PHRASES[cfg_name]),
                self.project,
                state_home=self.state_home,
            )
            self.assertEqual(code, 0)
        extra = {}
        if ROLE_TYPES[role] is not None:
            extra["agent_type"] = ROLE_TYPES[role]
        if agent_id is not None:
            extra["agent_id"] = agent_id
        tool_input = {k: self.subst(v) for k, v in tool_input.items()}
        payload = helpers.pre_tool_use("s1", tool, tool_input, **extra)
        code, out, _err = helpers.run_hook(
            "PreToolUse", payload, self.project, state_home=self.state_home
        )
        self.assertEqual(code, 0)
        return out

    def check(self, cfg_name, role, tool, tool_input, expected, contains=(), agent_id=None):
        out = self.call(cfg_name, role, tool, tool_input, agent_id=agent_id)
        if expected == ALLOW:
            self.assertIsNone(out)
            return
        self.assertIsNotNone(out)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        reason = hso["permissionDecisionReason"]
        self.assertTrue(reason)
        entry = self.last_log()
        self.assertEqual(entry["ev"], "deny")
        self.assertEqual(entry["rule"], expected)
        for word in contains:
            self.assertIn(word, reason)


def fp(p):
    return {"file_path": p}


def path(p):
    return {"path": p}


def cmd(c):
    return {"command": c}


ROWS = []


def rows(rule, cfg, role, tool, items):
    for inp, exp in items:
        ROWS.append((rule, cfg, role, tool, inp, exp))


D5, D6, D7 = "5", "6", "7"

for _role in ("tester", "specguard:tester"):
    rows("GR-1", "ROLES", _role, "Read", [(fp("src/a.ts"), D5)])
rows("GR-1", "ROLES", "tester", "Read", [
    (fp("<project>/packages/x/src/deep/b.ts"), D5),
    (fp("lib/core/x.ts"), D5),
    (fp("lib/core"), D5),
    (fp("docs/../src/a.ts"), D5),
    (fp("src/db/schema.ts.bak"), D5),
    (fp("src/db/schema.ts"), ALLOW),
    (fp("lib/corex/y.ts"), ALLOW),
    (fp("source/a.ts"), ALLOW),
    (fp("docs/specs/x.md"), ALLOW),
    (fp("<outside>/src/a.ts"), ALLOW),
])
rows("GR-1", "ROLES", "tester", "Write", [
    (fp("src/new.ts"), D5),
    (fp("test/a.spec.ts"), ALLOW),
    (fp(".specguard/reports/tester/x.md"), ALLOW),
])
rows("GR-1", "ROLES", "tester", "Edit", [(fp("config/secrets.env"), D5)])
rows("GR-1", "ROLES", "tester", "NotebookEdit", [({"notebook_path": "src/n.ipynb"}, D5)])
rows("GR-1", "NESTED", "tester", "Read", [
    (fp("app/src/a.ts"), D5),
    (fp("src/a.ts"), ALLOW),
    (fp("app/test/a.ts"), ALLOW),
])

rows("GR-2", "ROLES", "tester", "Grep", [
    (path("test"), ALLOW),
    (path("qa/suites"), ALLOW),
    (path("docs/specs"), ALLOW),
    (path("app.spec.ts"), ALLOW),
    (path("src/db/schema.ts"), ALLOW),
    (path("src"), D5),
    (path("."), D5),
    ({"pattern": "x"}, D5),
    (path("<outside>"), D5),
    (path(".specguard"), D5),
    (path("src/test"), D5),
])
rows("GR-2", "NESTED", "tester", "Grep", [(path("notes"), D5)])

rows("GR-3", "ROLES", "tester", "Glob", [
    ({"pattern": "src/**/*.ts"}, D5),
    ({"pattern": "**/src/*.ts"}, D5),
    ({"pattern": "lib/core/*.ts"}, D5),
    ({"pattern": "**/secrets.env"}, D5),
    ({"pattern": "*.ts", "path": "src"}, D5),
    ({"pattern": "*.ts", "path": "lib/core"}, D5),
    ({"pattern": "test/**/*.ts"}, ALLOW),
    ({"pattern": "*.md", "path": "docs"}, ALLOW),
    ({"pattern": "source/*.ts"}, ALLOW),
    ({"pattern": "*.ts"}, ALLOW),
    ({"pattern": "*.ts", "path": "src/db/schema.ts"}, ALLOW),
])

rows("GR-4", "ROLES", "tester", "Bash", [
    (cmd("ls src"), D5),
    (cmd("cat packages/domain/src/phone.ts"), D5),
    (cmd("ls ./src/"), D5),
    (cmd("cat x/'src'/a.ts"), D5),
    (cmd("cd app && grep -rn x src"), D5),
    (cmd('bash -c "ls src"'), D5),
    (cmd("find . -path '*src*'"), D5),
    (cmd('ps aux | grep -i "fake\\|lib/core/index"'), D5),
    (cmd("cat config/secrets.env"), D5),
    (cmd("ls lib/core"), D5),
    (cmd('ls "lib/core"'), D5),
    (cmd("cat 'lib/core/x.ts'"), D5),
    (cmd("ls lib/corex"), ALLOW),
    (cmd("ls source"), ALLOW),
    (cmd("ls my-src"), ALLOW),
    (cmd("cat old-secrets.env"), ALLOW),
    (cmd("cat secrets.env.example"), ALLOW),
    (cmd("ls test"), ALLOW),
    (cmd("cat docs/specs/x.md"), ALLOW),
    (cmd("python3 -m unittest discover -s test"), ALLOW),
    (cmd("git status"), ALLOW),
    (cmd("cat src/db/schema.ts"), ALLOW),
    (cmd("cat src/db/schema.ts src/a.ts"), D5),
])
rows("GR-5", "ROLES", "tester", "Bash", [
    (cmd("cat package.json | grep -A5 '\"src\"'"), ALLOW),
    (cmd("grep -n 'hello' test/a.spec.ts"), ALLOW),
    (cmd('ls "src"'), D5),
    (cmd("grep -rn x 'src'"), D5),
    (cmd("cat 'src/a.ts'"), D5),
    (cmd("cat 'my src'"), D5),
])
rows("GR-6", "ROLES", "tester", "Bash", [
    (cmd("cat src/db/schema.ts.bak"), D5),
    (cmd("cat src/db/schema.tsx"), D5),
])

rows("GR-8", "ROLES", "advocate", "Edit", [(fp(".specguard/reports/spec-review/x.md"), D6)])
rows("GR-8", "ROLES", "advocate", "MultiEdit", [(fp("docs/specs/x.md"), D6)])
rows("GR-8", "ROLES", "advocate", "NotebookEdit", [({"notebook_path": "n.ipynb"}, D6)])
rows("GR-8", "ROLES", "advocate", "Bash", [(cmd("ls docs"), D6), (cmd("git status"), D6)])
rows("GR-8", "ROLES", "advocate", "Write", [
    (fp(".specguard/reports/spec-review/x-2026-09-29.md"), ALLOW),
    (fp(".specguard/reports/tester/x.md"), D6),
    (fp("docs/specs/x.md"), D6),
    (fp(".specguard/reports/spec-review-evil/x.md"), D6),
    (fp(".specguard/reports/spec-review/../../../src/a.ts"), D6),
    ({}, D6),
])
rows("GR-8", "ROLES", "specguard:devils-advocate", "Write", [
    (fp(".specguard/reports/spec-review/sub/y.md"), ALLOW),
])

rows("GR-10", "ROLES", "advocate", "Read", [
    (fp("src/a.ts"), D6),
    (fp("config/secrets.env"), D6),
    (fp("lib/core/x.ts"), D6),
    (fp(".specguard/reports/tester/t.md"), D6),
    (fp("src/db/schema.ts"), ALLOW),
    (fp("docs/specs/x.md"), ALLOW),
    (fp("test/a.spec.ts"), ALLOW),
    (fp(".specguard/reports/spec-review/x.md"), ALLOW),
])
rows("GR-11", "ROLES", "advocate", "Grep", [
    (path("test"), ALLOW),
    (path("docs"), ALLOW),
    (path("qa/suites"), ALLOW),
    (path("src/db/schema.ts"), ALLOW),
    (path("<outside>"), ALLOW),
    (path("src"), D6),
    (path("."), D6),
    (path("<project>"), D6),
    (path("<parent>"), D6),
    (path(".specguard"), D6),
    (path(".specguard/reports/tester"), D6),
    (path("src/test"), D6),
    ({"pattern": "x"}, D6),
])
rows("GR-11", "NESTED", "advocate", "Grep", [
    (path("notes"), ALLOW),
    (path("app/test"), ALLOW),
    (path("app/docs/specs"), ALLOW),
    (path("app"), D6),
    (path("."), D6),
    (path("app/lib"), D6),
    (path("app/src"), D6),
])
rows("GR-12", "ROLES", "advocate", "Glob", [
    ({"pattern": "src/**/*.ts"}, D6),
    ({"pattern": "*.ts", "path": "lib/core"}, D6),
    ({"pattern": "docs/**/*.md"}, ALLOW),
])

rows("GR-13", "MODE_feature", "executor", "Edit", [(fp("test/a.ts"), D7)])
rows("GR-13", "MODE_feature", "executor", "Write", [
    (fp("packages/x/test/a.ts"), D7),
    (fp("qa/suites/case.py"), D7),
    (fp("src/app.spec.ts"), D7),
    (fp("qa/suitesx/a.ts"), ALLOW),
    (fp("contest/a.ts"), ALLOW),
    (fp("src/a.ts"), ALLOW),
    (fp("docs/specs/x.md"), ALLOW),
    (fp("<outside>/test/a.ts"), ALLOW),
])
rows("GR-13", "MODE_feature", "executor", "MultiEdit", [(fp("test/a.ts"), D7)])
rows("GR-13", "MODE_feature", "executor", "NotebookEdit", [({"notebook_path": "test/n.ipynb"}, D7)])
rows("GR-13", "MODE_feature", "oh-my-claudecode:executor", "Write", [(fp("test/a.ts"), D7)])
for _m in ("hard", "visual+feature", "visual+hard"):
    rows("GR-13", "MODE_" + _m, "executor", "Write", [(fp("test/a.ts"), D7)])
for _m in ("simple", "visual"):
    rows("GR-13", "MODE_" + _m, "executor", "Write", [(fp("test/a.ts"), ALLOW)])
rows("GR-13", "MODE_feature", "executor", "Read", [(fp("test/a.ts"), ALLOW)])
rows("GR-13", "MODE_feature", "executor", "Bash", [(cmd("echo x > test/a.ts"), ALLOW)])
rows("GR-13", "MODE_feature", "tester", "Write", [(fp("test/a.spec.ts"), ALLOW)])


class RolesTable(RolesBase):
    pass


def _slug(text):
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


def _make(cfg, role, tool, inp, exp):
    def test(self):
        self.check(cfg, role, tool, inp, exp)

    return test


for _i, (_rule, _cfg, _role, _tool, _inp, _exp) in enumerate(ROWS):
    _verdict = "allow" if _exp == ALLOW else "deny_rule_" + _exp
    _name = "test_{}_{:03d}_{}_{}_{}_{}_{}".format(
        _rule.replace("-", "_"), _i, _slug(_cfg), _slug(_role), _tool,
        _slug(json.dumps(_inp, sort_keys=True))[:60], _verdict,
    )
    setattr(RolesTable, _name, _make(_cfg, _role, _tool, _inp, _exp))


class RolesTests(RolesBase):
    def test_GR_7_grep_deny_reason_names_all_allowed_roots(self):
        self.check("ROLES", "tester", "Grep", path("src"), D5,
                   contains=("test", "qa/suites", "docs"))

    def test_GR_7_bash_deny_reason_names_all_allowed_roots(self):
        self.check("ROLES", "tester", "Bash", cmd("ls src"), D5,
                   contains=("test", "qa/suites", "docs"))

    def test_GR_9_allowed_advocate_write_is_recorded_with_agent_id(self):
        self.check("ROLES", "advocate", "Write", fp(".specguard/reports/spec-review/x.md"),
                   ALLOW, agent_id="a1")
        with open(os.path.join(self.state_dir(), "advocate-reports.json"), encoding="utf-8") as f:
            data = json.load(f)
        target = os.path.join(self.project, ".specguard/reports/spec-review/x.md")
        self.assertIn(target, data)
        self.assertEqual([e["agent_id"] for e in data[target]], ["a1"])
        self.assertIsInstance(data[target][0]["at"], (int, float))

    def test_GR_9_denied_advocate_write_records_nothing(self):
        self.check("ROLES", "advocate", "Write", fp("docs/specs/x.md"), D6, agent_id="a1")
        p = os.path.join(self.state_dir(), "advocate-reports.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {})

    def test_GR_14_feature_reason_names_mode_and_escalation(self):
        self.check("MODE_feature", "executor", "Write", fp("test/a.ts"), D7,
                   contains=("feature", "rule ID"))

    def test_GR_14_hard_reason_names_hard(self):
        self.check("MODE_hard", "executor", "Write", fp("test/a.ts"), D7,
                   contains=("hard", "rule ID"))

    def test_GR_14_visual_hard_reason_names_hard(self):
        self.check("MODE_visual+hard", "executor", "Write", fp("test/a.ts"), D7,
                   contains=("hard", "rule ID"))


class NestedFeatureTests(RolesBase):
    def check_nested(self, rel, expected):
        cfg = make_config("NESTED")
        cfg["modes"]["default"] = "feature"
        helpers.make_project(self.project, config=cfg)
        payload = helpers.pre_tool_use("s1", "Write", {"file_path": rel})
        code, out, _ = helpers.run_hook("PreToolUse", payload, self.project, state_home=self.state_home)
        self.assertEqual(code, 0)
        if expected == ALLOW:
            self.assertIsNone(out)
        else:
            self.assertIsNotNone(out)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(self.last_log()["rule"], expected)

    def test_GR_13_nested_repo_test_path_is_locked(self):
        self.check_nested("app/test/a.ts", D7)

    def test_GR_13_nested_path_outside_repo_is_not_locked(self):
        self.check_nested("test/a.ts", ALLOW)


if __name__ == "__main__":
    unittest.main()
