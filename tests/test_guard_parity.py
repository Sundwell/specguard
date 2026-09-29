import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _THIS_DIR)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import helpers  # noqa: E402

ORACLE = os.path.join(_THIS_DIR, "fixtures", "legacy-guard.py")

PARFUME_CONFIG = {
    "version": 1,
    "repo": "sample-shops",
    "hidden": {"segments": ["src", "scripts"], "names": [], "paths": []},
    "tester_readable": ["apps/api/src/db/schema.ts"],
    "tests": {"segments": ["test", "e2e"], "paths": [], "file_regex": r"\.(test|spec)\.ts$"},
    "docs_dirs": ["docs"],
    "specs_dir": "docs/specs",
    "modes": {"default": "feature", "approval_default": True},
}

# The parfume role x tool x path cases from
# ~/.omc/plans/agent-flows/audit/hooks/full_matrix.py, lines 21-54, reused verbatim (not imported).
CASES = [
    ("Read", {"file_path": "sample-shops/apps/api/src/foo.ts"}),
    ("Read", {"file_path": "sample-shops/apps/api/test/foo.test.ts"}),
    ("Read", {"file_path": "sample-shops/docs/specs/foo.md"}),
    ("Read", {"file_path": "sample-shops/apps/api/src/db/schema.ts"}),  # tester_readable exception
    ("Edit", {"file_path": "sample-shops/apps/api/test/foo.test.ts", "old_string": "a", "new_string": "b"}),
    ("Edit", {"file_path": "sample-shops/apps/api/src/foo.ts", "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": "sample-shops/apps/api/test/foo.test.ts", "content": "x"}),
    ("MultiEdit", {"file_path": "sample-shops/apps/api/src/foo.ts", "edits": []}),
    ("NotebookEdit", {"notebook_path": "sample-shops/apps/api/src/nb.ipynb"}),
    ("Grep", {"pattern": "foo", "path": "sample-shops/apps/api/test"}),
    ("Grep", {"pattern": "foo", "path": "sample-shops/apps/api/src"}),
    ("Grep", {"pattern": "foo"}),  # no path at all
    ("Glob", {"pattern": "sample-shops/apps/api/src/**/*.ts"}),  # tool NOT in old matcher, NEW guard
    ("Bash", {"command": "cat sample-shops/apps/api/src/foo.ts"}),
    ("Bash", {"command": "sed -n 1p sample-shops/apps/api/src/foo.ts"}),
    ("Bash", {"command": "git show HEAD:sample-shops/apps/api/src/foo.ts"}),
    ("Bash", {"command": "git diff"}),
    ("Bash", {"command": "rg foo sample-shops/apps/api/test"}),
    ("Bash", {"command": "find sample-shops -name '*.ts'"}),
    ("Bash", {"command": "cat sample-shops/apps/api/srcbogus/foo.ts"}),  # 'src' substring, not a segment
    ("Read", {"file_path": "../outside-repo/secrets.txt"}),  # traversal
    ("Read", {"file_path": "/etc/passwd"}),  # absolute outside repo
    ("Read", {"file_path": "sample-shops/apps/api"}),  # package root containing src, no trailing slash
    ("Read", {"file_path": "sample-shops/apps/api/src"}),  # folder named src, no trailing slash
]

ROLES = [
    ("executor", None, None),
    ("tester", "tester", "specguard:tester"),
    ("devils-advocate", "devils-advocate", "specguard:devils-advocate"),
]

# Cases where specguard intentionally differs from the old guard's decision, tagged with plan-lean
# section 5's id. Key is (role, tool, path/command short form).
INTENDED = {
    ("tester", "Glob", "sample-shops/apps/api/src/**/*.ts"): "I-2",
    ("devils-advocate", "Glob", "sample-shops/apps/api/src/**/*.ts"): "I-2",
}


def _short(tool_input):
    return tool_input.get("file_path") or tool_input.get("command") or tool_input.get("path") \
        or tool_input.get("pattern") or tool_input.get("notebook_path") or str(tool_input)


def _oracle_decision(tool_name, tool_input, cwd, agent_type):
    payload = {"tool_name": tool_name, "tool_input": tool_input, "cwd": cwd}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = cwd
    proc = subprocess.run(
        [sys.executable, "-B", ORACLE],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=10,
    )
    if proc.returncode == 2:
        return "DENY"
    if proc.returncode == 0:
        return "ALLOW"
    raise AssertionError("oracle rc={} stderr={}".format(proc.returncode, proc.stderr))


def _new_decision(tool_name, tool_input, project_dir, agent_type, state_home):
    payload = helpers.pre_tool_use("parity", tool_name, tool_input, agent_type=agent_type)
    code, out, err = helpers.run_hook("PreToolUse", payload, project_dir, state_home=state_home)
    if code != 0:
        raise AssertionError("hook exit {} stderr={}".format(code, err))
    if out and out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
        return "DENY"
    return "ALLOW"


class GuardParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_dir = tempfile.mkdtemp(prefix="specguard-parity-")
        helpers.make_project(cls.project_dir, config=PARFUME_CONFIG)
        cls.state_home = tempfile.mkdtemp(prefix="specguard-parity-state-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project_dir, ignore_errors=True)
        shutil.rmtree(cls.state_home, ignore_errors=True)

    def test_parity_matrix(self):
        hits = []
        untagged = []

        for role, bare_agent, scoped_agent in ROLES:
            for tool, tool_input in CASES:
                with self.subTest(role=role, tool=tool, path=_short(tool_input)):
                    old = _oracle_decision(tool, tool_input, self.project_dir, bare_agent)

                    new_bare = _new_decision(tool, tool_input, self.project_dir, bare_agent, self.state_home)
                    new_scoped = _new_decision(tool, tool_input, self.project_dir, scoped_agent, self.state_home)
                    self.assertEqual(
                        new_bare, new_scoped,
                        "bare {!r} and scoped {!r} must decide identically".format(bare_agent, scoped_agent),
                    )

                    key = (role, tool, _short(tool_input))
                    intended = INTENDED.get(key)

                    if new_bare != old:
                        if intended is None:
                            untagged.append((role, tool, _short(tool_input), old, new_bare))
                        else:
                            hits.append((role, tool, _short(tool_input), old, new_bare, intended))
                    elif intended is not None:
                        # Documented as an intended difference but did not fire - not a defect,
                        # but worth surfacing rather than silently dropping the case.
                        hits.append((role, tool, _short(tool_input), old, new_bare, intended + " (no-op)"))

        print("\nintended differences hit:")
        print("{:16} | {:10} | {:55} | {:6} | {:6} | {}".format(
            "role", "tool", "case", "old", "new", "id"))
        for role, tool, case, old, new, intended in hits:
            print("{:16} | {:10} | {:55} | {:6} | {:6} | {}".format(role, tool, case[:55], old, new, intended))

        self.assertEqual(untagged, [], "untagged differences from the old guard: {}".format(untagged))
        self.assertEqual(len(hits), 2, "expected exactly the two I-2 Glob hits, got {}".format(hits))


if __name__ == "__main__":
    unittest.main()
