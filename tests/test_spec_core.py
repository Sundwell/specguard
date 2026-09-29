import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402

NOTICE = "specguard: hook error, checks off, see errors.log"
BROKEN_JSON = "{ this is not json"
NOT_OBJECT = "[1, 2, 3]"
WRONG_TYPE = '{"version": 1, "hidden": "oops"}'
BROKEN = (("broken-json", BROKEN_JSON), ("not-object", NOT_OBJECT), ("wrong-type", WRONG_TYPE))
OK_CONFIG = {"version": 1}
QA_ROLES_BROKEN = '{"version": 1, "roles": {"tester": ["qa-writer"]}, "hidden": "oops"}'
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def hook_env(project, state_home, plugin_root=None, extra=None, with_project=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    if with_project:
        env["CLAUDE_PROJECT_DIR"] = project
    env["CLAUDE_PLUGIN_ROOT"] = plugin_root or helpers.REPO_ROOT
    env["XDG_STATE_HOME"] = state_home
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def payload_for(event, payload, cwd):
    body = dict(payload)
    body.setdefault("hook_event_name", event)
    body.setdefault("cwd", cwd)
    return body


def walk(root):
    found = []
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            found.append(os.path.relpath(os.path.join(base, name), root))
    return sorted(found)


def digest(files):
    h = hashlib.sha256()
    for rel in sorted(files):
        content = files[rel]
        if isinstance(content, str):
            content = content.encode("utf-8")
        h.update(rel.encode("utf-8") + b"\0" + content + b"\0")
    return h.hexdigest()


def stop_cfg(**over):
    cfg = {
        "dirty_pathspec": ["."],
        "fresh_paths": ["."],
        "fresh_globs": ["*.py"],
        "exclude_dirs": [".git"],
        "run": "true",
        "timeout": 300,
    }
    cfg.update(over)
    return cfg


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sg-core-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = os.path.join(self.tmp, "proj")
        self.state_home = os.path.join(self.tmp, "state")
        os.makedirs(self.state_home)
        self.key = self.project.replace("/", "-")
        self.state_dir = os.path.join(self.state_home, "specguard", self.key)

    def build(self, cfg=None, raw=None, files=None):
        helpers.make_project(self.project, config=cfg, files=files)
        if raw is not None:
            os.makedirs(os.path.join(self.project, ".claude"), exist_ok=True)
            with open(os.path.join(self.project, ".claude", "specguard.json"), "w", encoding="utf-8") as f:
                f.write(raw)

    def hook(self, event, payload, **kw):
        kw.setdefault("state_home", self.state_home)
        return helpers.run_hook(event, payload, self.project, **kw)

    def pre(self, agent_type=None, tool="Read", tool_input=None, sid="s1"):
        payload = helpers.pre_tool_use(sid, tool, tool_input or {"file_path": "a.py"}, agent_type=agent_type)
        code, out, err = self.hook("PreToolUse", payload)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        return out

    def prompt(self, text="please continue", sid="s1", agent_type=None):
        payload = helpers.user_prompt_submit(sid, text, agent_type=agent_type)
        code, out, err = self.hook("UserPromptSubmit", payload)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        return out

    def raw(self, stdin_text, project=None, env_extra=None, with_project=True, cwd=None):
        env = hook_env(project or self.project, self.state_home, extra=env_extra, with_project=with_project)
        return subprocess.run([sys.executable, "-B", helpers.HOOK_PATH], input=stdin_text,
                              capture_output=True, text=True, env=env, cwd=cwd or self.tmp, timeout=20)

    def write_state(self, name, content):
        path = os.path.join(self.state_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def session(self, sid, obj):
        self.write_state("sessions/{}.json".format(sid), obj)

    def read_state(self, name):
        with open(os.path.join(self.state_dir, name), encoding="utf-8") as f:
            return f.read()

    def agents(self):
        path = os.path.join(self.state_dir, "agents.json")
        if not os.path.exists(path):
            return {}
        return json.loads(self.read_state("agents.json"))

    def assertDeny(self, out, contains="guard error"):
        self.assertIsNotNone(out)
        self.assertEqual(set(out), {"hookSpecificOutput"})
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        self.assertEqual(spec["permissionDecision"], "deny")
        self.assertIn(contains, spec["permissionDecisionReason"])

    def context(self, out):
        if not out:
            return None
        return out.get("hookSpecificOutput", {}).get("additionalContext")

    def cli(self, flag, raw=None, cfg=None, plugin_root=None, with_project=True, state_home=None, cwd=None):
        if cfg is not None:
            raw = json.dumps(cfg)
        os.makedirs(self.project, exist_ok=True)
        if raw is not None:
            self.build(raw=raw)
        env = hook_env(self.project, state_home or self.state_home, plugin_root=plugin_root,
                       with_project=with_project)
        return subprocess.run([sys.executable, "-B", helpers.HOOK_PATH, flag], capture_output=True,
                              text=True, env=env, cwd=cwd or self.tmp, timeout=20)


class NoConfigTests(Base):
    def test_CO_1_no_config_gets_nothing_from_any_event(self):
        os.makedirs(self.project)
        rows = (
            ("PreToolUse", helpers.pre_tool_use("s1", "Read", {"file_path": "a.py"})),
            ("UserPromptSubmit", helpers.user_prompt_submit("s1", "go feature spec")),
            ("SubagentStart", helpers.subagent_start("s1", "a1", "specguard:tester")),
        )
        for event, payload in rows:
            with self.subTest(event=event):
                code, out, err = self.hook(event, payload)
                self.assertEqual((code, out, err), (0, None, ""))
                self.assertEqual(os.listdir(self.state_home), [])

    def test_CO_3_no_config_string_stdin_is_silent(self):
        os.makedirs(self.project)
        proc = self.raw('"a string"')
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))


class ProjectLookupTests(Base):
    def setUp(self):
        super().setUp()
        self.dir_a = os.path.join(self.tmp, "a")
        self.dir_b = os.path.join(self.tmp, "b")
        os.makedirs(self.dir_a)
        os.makedirs(self.dir_b)

    def cfg(self, path, raw):
        os.makedirs(os.path.join(path, ".claude"), exist_ok=True)
        with open(os.path.join(path, ".claude", "specguard.json"), "w") as f:
            f.write(raw)

    def read_as_tester(self, project_dir, cwd, env_project=True, env_extra=None):
        payload = payload_for("PreToolUse", helpers.pre_tool_use("s1", "Read", {"file_path": "a.py"},
                                                                  agent_type="tester"), cwd)
        proc = subprocess.run(
            [sys.executable, "-B", helpers.HOOK_PATH], input=json.dumps(payload), capture_output=True,
            text=True, cwd=cwd, timeout=20,
            env=hook_env(project_dir, self.state_home, with_project=env_project, extra=env_extra))
        self.assertEqual(proc.returncode, 0)
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def test_CO_2_project_dir_env_wins_over_payload_cwd(self):
        self.cfg(self.dir_a, "{")
        out = self.read_as_tester(self.dir_a, self.dir_b)
        self.assertDeny(out)

    def test_CO_2_payload_cwd_config_is_not_looked_up_when_env_is_set(self):
        self.cfg(self.dir_b, "{")
        self.assertIsNone(self.read_as_tester(self.dir_a, self.dir_b))

    def test_CO_2_cwd_is_used_when_env_is_unset(self):
        self.cfg(self.dir_b, "{")
        self.assertDeny(self.read_as_tester(self.dir_a, self.dir_b, env_project=False))

    def test_CO_2_cwd_is_used_when_env_is_empty(self):
        self.cfg(self.dir_b, "{")
        out = self.read_as_tester(self.dir_a, self.dir_b, env_extra={"CLAUDE_PROJECT_DIR": ""})
        self.assertDeny(out)


class EntrypointTests(Base):
    def test_CO_3_odd_stdin_exits_zero_silently(self):
        self.build(cfg=OK_CONFIG)
        for name, text in (("empty", ""), ("not json", "not json {"), ("array", "[1, 2]")):
            with self.subTest(stdin=name):
                proc = self.raw(text)
                self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))

    def test_CO_4_unrouted_events_print_nothing(self):
        self.build(cfg=OK_CONFIG)
        rows = ('{"hook_event_name": "Elicitation", "session_id": "s1"}', '{"session_id": "s1"}')
        for text in rows:
            with self.subTest(stdin=text):
                proc = self.raw(text)
                self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_CO_5_allowed_call_prints_nothing_not_allow(self):
        self.build(cfg=OK_CONFIG)
        rows = (("Read", {"file_path": "notes.txt"}), ("Bash", {"command": "ls"}))
        for tool, tool_input in rows:
            with self.subTest(tool=tool):
                self.assertIsNone(self.pre(tool=tool, tool_input=tool_input))


class FailSafeTests(Base):
    def test_CO_6_role_is_refused_on_broken_config(self):
        rows = (
            (BROKEN_JSON, "Read", "specguard:tester"),
            (BROKEN_JSON, "Read", "specguard:devils-advocate"),
            (NOT_OBJECT, "Grep", "tester"),
            (WRONG_TYPE, "Read", "specguard:tester"),
        )
        for raw, tool, agent in rows:
            with self.subTest(config=raw, agent=agent):
                self.build(raw=raw)
                self.assertDeny(self.pre(agent_type=agent, tool=tool))

    def test_CO_6_refusal_repeats_on_every_call(self):
        self.build(raw=BROKEN_JSON)
        for _ in range(3):
            self.assertDeny(self.pre(agent_type="tester"))

    def test_CO_7_executor_is_told_once_per_session(self):
        for name, raw in BROKEN:
            with self.subTest(config=name):
                self.setUp()
                self.build(raw=raw)
                self.assertEqual(self.pre(), {"systemMessage": NOTICE})
                self.assertIsNone(self.pre())
                self.assertEqual(self.pre(sid="s2"), {"systemMessage": NOTICE})
                self.assertIsNone(self.pre(sid="s2"))

    def test_CO_7_notice_on_bash_has_no_decision(self):
        self.build(raw=NOT_OBJECT)
        out = self.pre(tool="Bash", tool_input={"command": "ls"})
        self.assertEqual(out, {"systemMessage": NOTICE})

    def test_CO_8_role_detection_without_readable_config(self):
        rows = (
            ("tester", True), ("foo:tester", True), ("x:devils-advocate", True),
            ("testers", False), ("mytester", False), ("tester2", False), ("executor", False),
        )
        for i, (agent, is_role) in enumerate(rows):
            with self.subTest(agent=agent):
                self.build(raw=BROKEN_JSON)
                out = self.pre(agent_type=agent, sid="r{}".format(i))
                if is_role:
                    self.assertDeny(out)
                else:
                    self.assertEqual(out, {"systemMessage": NOTICE})

    def test_CO_8_roles_object_decides_when_json_is_readable(self):
        rows = (("qa-writer", True), ("tester", False), ("specguard:devils-advocate", True))
        for i, (agent, is_role) in enumerate(rows):
            with self.subTest(agent=agent):
                self.build(raw=QA_ROLES_BROKEN)
                out = self.pre(agent_type=agent, sid="q{}".format(i))
                if is_role:
                    self.assertDeny(out)
                else:
                    self.assertEqual(out, {"systemMessage": NOTICE})

    def test_CO_9_prompt_by_executor_gets_notice_only(self):
        self.build(raw=BROKEN_JSON)
        self.assertEqual(self.prompt(), {"systemMessage": NOTICE})

    def test_CO_9_prompt_by_role_gets_notice_without_decision(self):
        self.build(raw=BROKEN_JSON)
        self.assertEqual(self.prompt(agent_type="tester"), {"systemMessage": NOTICE})

    def test_CO_9_stop_by_role_gets_notice_without_decision(self):
        self.build(raw=BROKEN_JSON)
        code, out, _ = self.hook("Stop", helpers.stop("s1", agent_type="tester"))
        self.assertEqual((code, out), (0, {"systemMessage": NOTICE}))

    def test_CO_9_counter_is_shared_by_events_of_a_session(self):
        self.build(raw=BROKEN_JSON)
        self.assertEqual(self.pre(), {"systemMessage": NOTICE})
        code, out, _ = self.hook("Stop", helpers.stop("s1"))
        self.assertEqual((code, out), (0, None))
        self.setUp()
        self.build(raw=BROKEN_JSON)
        code, out, _ = self.hook("SessionStart", helpers.session_start("s1"))
        self.assertEqual((code, out), (0, {"systemMessage": NOTICE}))
        self.assertIsNone(self.pre())

    def test_CO_10_hard_mode_refuses_tester_launch(self):
        def agent(kind):
            return {"subagent_type": kind, "description": "d", "prompt": "p"}
        rows = (
            ("hard tester specguard", {"modes": ["hard"]}, "Agent", agent("specguard:tester"), True),
            ("hard tester", {"modes": ["hard"]}, "Agent", agent("tester"), True),
            ("visual+hard tester", {"modes": ["visual", "hard"]}, "Agent", agent("tester"), True),
            ("hard Explore", {"modes": ["hard"]}, "Agent", agent("Explore"), False),
            ("hard Read", {"modes": ["hard"]}, "Read", {"file_path": "a.py"}, False),
            ("feature tester", {"modes": ["feature"]}, "Agent", agent("tester"), False),
            ("no session tester", None, "Agent", agent("tester"), False),
        )
        for name, sess, tool, tool_input, denied in rows:
            with self.subTest(row=name):
                self.setUp()
                self.build(raw=BROKEN_JSON)
                if sess is not None:
                    self.session("s1", sess)
                out = self.pre(tool=tool, tool_input=tool_input)
                if denied:
                    self.assertDeny(out, "hard mode")
                    self.assertDeny(self.pre(tool=tool, tool_input=tool_input), "hard mode")
                else:
                    self.assertEqual(out, {"systemMessage": NOTICE})

    def test_CO_10_hard_mode_does_not_refuse_a_prompt(self):
        self.build(raw=BROKEN_JSON)
        self.session("s1", {"modes": ["hard"]})
        self.assertEqual(self.prompt(), {"systemMessage": NOTICE})

    def test_CO_11_failure_writes_errors_log_not_log_jsonl(self):
        self.build(raw=BROKEN_JSON)
        self.pre()
        self.assertTrue(self.read_state("errors.log").strip())
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, "log.jsonl")))

    def test_CO_11_every_failing_call_appends(self):
        self.build(raw=BROKEN_JSON)
        self.pre()
        first = len(self.read_state("errors.log"))
        self.pre()
        self.assertGreater(len(self.read_state("errors.log")), first)


class StatusLineTests(Base):
    def test_CO_12_default_status_line(self):
        rows = (
            (OK_CONFIG, "Active specguard mode is simple."),
            ({"version": 1, "modes": {"default": "feature"}}, "Active specguard mode is feature, approval on."),
            ({"version": 1, "modes": {"default": "feature", "approval_default": False}},
             "Active specguard mode is feature, approval off."),
            ({"version": 1, "modes": {"default": "hard", "approval_default": False}},
             "Active specguard mode is hard, approval on."),
            ({"version": 1, "modes": {"default": "visual"}}, "Active specguard mode is visual."),
        )
        for cfg, expected in rows:
            with self.subTest(expected=expected, cfg=cfg):
                self.setUp()
                self.build(cfg=cfg)
                out = self.prompt()
                self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
                self.assertEqual(self.context(out), expected)

    def test_CO_13_14_session_file_replaces_defaults(self):
        feature = {"version": 1, "modes": {"default": "feature"}}
        rows = (
            (OK_CONFIG, {"modes": ["feature"], "approval": False}, "Active specguard mode is feature, approval off."),
            (feature, {"modes": ["visual", "feature"], "approval": True},
             "Active specguard mode is visual+feature, approval on."),
            (feature, {"modes": ["simple"]}, "Active specguard mode is simple."),
            (OK_CONFIG, {"modes": ["hard"], "approval": False}, "Active specguard mode is hard, approval on."),
            (OK_CONFIG, {"modes": ["visual", "hard"], "approval": False},
             "Active specguard mode is visual+hard, approval on."),
        )
        for cfg, sess, expected in rows:
            with self.subTest(session=sess):
                self.setUp()
                self.build(cfg=cfg)
                self.session("s1", sess)
                self.assertEqual(self.context(self.prompt()), expected)

    def test_CO_13_other_sessions_do_not_leak(self):
        self.build(cfg={"version": 1, "modes": {"default": "feature"}})
        self.session("other", {"modes": ["hard"]})
        self.assertEqual(self.context(self.prompt()), "Active specguard mode is feature, approval on.")

    def test_CO_15_role_gets_no_line_by_roles_lists(self):
        plain = {"version": 1, "modes": {"default": "feature"}}
        qa = {"version": 1, "modes": {"default": "feature"}, "roles": {"tester": ["qa-writer"]}}
        rows = (
            (plain, "specguard:tester", None),
            (plain, "specguard:devils-advocate", None),
            (qa, "qa-writer", None),
            (qa, "tester", "Active specguard mode is feature, approval on."),
        )
        for cfg, agent, expected in rows:
            with self.subTest(agent=agent, cfg=cfg):
                self.setUp()
                self.build(cfg=cfg)
                self.assertEqual(self.context(self.prompt(agent_type=agent)), expected)


class AgentsFileTests(Base):
    def start(self, sid, agent_id, agent_type):
        code, _, _ = self.hook("SubagentStart", helpers.subagent_start(sid, agent_id, agent_type))
        self.assertEqual(code, 0)

    def test_CO_16_records_keeps_and_replaces(self):
        self.build(cfg=OK_CONFIG)
        self.start("s1", "a1", "specguard:tester")
        self.assertEqual(self.agents(), {"a1": {"session_id": "s1", "agent_type": "specguard:tester"}})
        self.start("s1", "a2", "Explore")
        self.assertEqual(self.agents(), {"a1": {"session_id": "s1", "agent_type": "specguard:tester"},
                                         "a2": {"session_id": "s1", "agent_type": "Explore"}})
        self.start("s2", "a1", "Explore")
        self.assertEqual(self.agents(), {"a1": {"session_id": "s2", "agent_type": "Explore"},
                                         "a2": {"session_id": "s1", "agent_type": "Explore"}})

    def test_CO_17_concurrent_processes_lose_no_record(self):
        self.build(cfg=OK_CONFIG)
        env = hook_env(self.project, self.state_home)
        procs = []
        for i in range(8):
            payload = payload_for("SubagentStart", helpers.subagent_start("s1", "a{}".format(i), "Explore"),
                                  self.project)
            p = subprocess.Popen([sys.executable, "-B", helpers.HOOK_PATH], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            p.stdin.write(json.dumps(payload))
            p.stdin.close()
            procs.append(p)
        for p in procs:
            p.wait(timeout=30)
            p.stdout.close()
            p.stderr.close()
            self.assertEqual(p.returncode, 0)
        self.assertEqual(sorted(self.agents()), ["a{}".format(i) for i in range(8)])

    def test_CO_18_held_lock_is_given_up_after_bounded_wait(self):
        self.build(cfg=OK_CONFIG)
        os.makedirs(self.state_dir)
        fd = os.open(os.path.join(self.state_dir, "agents.json.lock"), os.O_CREAT | os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        payload = payload_for("SubagentStart", helpers.subagent_start("s1", "a1", "Explore"), self.project)
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-B", helpers.HOOK_PATH], input=json.dumps(payload),
                              capture_output=True, text=True, env=hook_env(self.project, self.state_home),
                              timeout=30)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 6)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout), {"systemMessage": NOTICE})
        self.assertNotIn("a1", self.agents())

    def test_CO_18_free_lock_records_entry(self):
        self.build(cfg=OK_CONFIG)
        self.start("s1", "a1", "Explore")
        self.assertIn("a1", self.agents())


class StateLocationTests(Base):
    def test_CO_19_project_keeps_only_built_files(self):
        self.build(cfg=OK_CONFIG, files={"src/a.py": "x = 1\n"})
        before = walk(self.project)
        self.pre()
        self.prompt()
        self.hook("SubagentStart", helpers.subagent_start("s1", "a1", "Explore"))
        self.assertEqual(walk(self.project), before)
        self.assertIn("a1", self.agents())

    def test_CO_19_no_bytecode_under_plugin_root(self):
        self.build(cfg=OK_CONFIG)
        root = os.path.join(self.tmp, "plugin")
        os.makedirs(root)
        for name in ("scripts", "hooks", "phrases"):
            src = os.path.join(helpers.REPO_ROOT, name)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(root, name),
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        hook_path = os.path.join(root, "scripts", "specguard_hook.py")
        calls = (
            ("PreToolUse", helpers.pre_tool_use("s1", "Read", {"file_path": "a.py"})),
            ("UserPromptSubmit", helpers.user_prompt_submit("s1", "go feature spec")),
            ("SubagentStart", helpers.subagent_start("s1", "a1", "Explore")),
            ("SessionStart", helpers.session_start("s1")),
        )
        outs = []
        for event, payload in calls:
            code, out, _ = self.hook(event, payload, hook_path=hook_path, plugin_root=root)
            self.assertEqual(code, 0)
            outs.append(out)
        self.assertIn("Active specguard mode is feature", self.context(outs[1]))
        offenders = [p for p in walk(root) if "__pycache__" in p or p.endswith(".pyc")]
        self.assertEqual(offenders, [])

    def test_CO_19_empty_xdg_falls_back_to_home_local_state(self):
        self.build(cfg=OK_CONFIG)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        code, _, _ = self.hook("SubagentStart", helpers.subagent_start("s1", "a1", "Explore"),
                               env={"XDG_STATE_HOME": "", "HOME": home})
        self.assertEqual(code, 0)
        path = os.path.join(home, ".local", "state", "specguard", self.key, "agents.json")
        with open(path, encoding="utf-8") as f:
            self.assertIn("a1", json.load(f))

    def test_CO_19_state_key_is_project_path_with_dashes(self):
        self.build(cfg=OK_CONFIG)
        other = os.path.join(self.tmp, "custom-x")
        self.hook("SubagentStart", helpers.subagent_start("s1", "a1", "Explore"), state_home=other)
        path = os.path.join(other, "specguard", self.project.replace("/", "-"), "agents.json")
        with open(path, encoding="utf-8") as f:
            self.assertIn("a1", json.load(f))


class CombinedOutputTests(Base):
    def test_CO_20_switch_announced_and_status_already_new(self):
        rows = (
            ("go feature spec", None, "specguard: mode feature, approval on",
             "Active specguard mode is feature, approval on."),
            ("go feature spec no approval", None, "specguard: mode feature, approval off",
             "Active specguard mode is feature, approval off."),
            ("go simple", {"modes": ["feature"]}, "specguard: mode simple, approval on",
             "Active specguard mode is simple."),
        )
        for text, sess, message, line in rows:
            with self.subTest(prompt=text):
                self.setUp()
                self.build(cfg=OK_CONFIG)
                if sess:
                    self.session("s1", sess)
                out = self.prompt(text)
                self.assertEqual(out["systemMessage"], message)
                self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
                self.assertIn(line, out["hookSpecificOutput"]["additionalContext"])

    def test_CO_20_plain_prompt_has_status_line_only(self):
        self.build(cfg=OK_CONFIG)
        out = self.prompt("please continue")
        self.assertNotIn("systemMessage", out)
        self.assertEqual(self.context(out), "Active specguard mode is simple.")

    def test_CO_21_session_start_names_guide_skill(self):
        self.build(cfg=OK_CONFIG)
        code, out, _ = self.hook("SessionStart", helpers.session_start("s1"))
        self.assertEqual(code, 0)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("specguard:guide", out["hookSpecificOutput"]["additionalContext"])

    def test_CO_27_expansion_mode_switch_carries_expansion_event_name(self):
        rows = (
            ("feature", "specguard: mode feature, approval on"),
            ("visual+feature", None),
        )
        for args, message in rows:
            with self.subTest(args=args):
                self.setUp()
                self.build(cfg=OK_CONFIG)
                payload = helpers.user_prompt_expansion("s1", "specguard:mode", args)
                code, out, err = self.hook("UserPromptExpansion", payload)
                self.assertEqual(code, 0)
                self.assertEqual(err, "")
                self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptExpansion")
                if message is not None:
                    self.assertIn("additionalContext", out["hookSpecificOutput"])
                    self.assertEqual(out["systemMessage"], message)

    def test_CO_27_plain_prompt_context_carries_submit_event_name(self):
        self.build(cfg=OK_CONFIG)
        out = self.prompt("please continue")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")

    def test_CO_27_refusal_carries_pre_tool_use_event_name(self):
        self.build(cfg={"version": 1, "hidden": {"segments": ["src"]}})
        out = self.pre(agent_type="specguard:tester", tool="Read", tool_input={"file_path": "src/x.py"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_CO_27_subagent_start_output_carries_its_own_event_name(self):
        self.build(cfg=OK_CONFIG)
        code, out, err = self.hook("SubagentStart", helpers.subagent_start("s1", "a1", "specguard:tester"))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        if out is not None:
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SubagentStart")

    def test_CO_27_post_tool_use_output_carries_its_own_event_name(self):
        self.build(cfg=OK_CONFIG)
        self.prompt("go feature spec")
        payload = helpers.post_tool_use("s1", "Read", {"file_path": "a.py"}, {"content": "x"})
        code, out, err = self.hook("PostToolUse", payload)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        if out is not None and "hookSpecificOutput" in out:
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")


FULL_CONFIG = {
    "version": 1,
    "roles": {"tester": ["tester"], "advocate": ["devils-advocate"]},
    "hidden": {"segments": ["src"], "names": [], "paths": []},
    "tests": {"segments": ["test"], "paths": [], "file_regex": r"\.test\.ts$"},
    "notes": {},
    "reports": {"advocate": ".specguard/reports/spec-review", "advocate_hidden": []},
    "modes": {"default": "feature", "approval_default": True},
    "stop": stop_cfg(),
    "visual": {"ui_paths": ["app/"], "deviations_log": "docs/d.md", "notes": ".claude/v.md"},
}


class CheckConfigTests(Base):
    def lines(self, proc):
        return proc.stdout.splitlines()

    def test_CO_22_valid_configs_print_ok(self):
        for name, cfg in (("minimal", OK_CONFIG), ("full", FULL_CONFIG)):
            with self.subTest(config=name):
                proc = self.cli("--check-config", cfg=cfg)
                self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "OK"))

    def test_CO_22_problem_lines_start_with_prefix(self):
        proc = self.cli("--check-config", cfg={"version": 1, "bogus": True})
        self.assertNotEqual(proc.returncode, 0)
        lines = self.lines(proc)
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("specguard: ") for line in lines))
        self.assertNotIn("OK", lines)

    def test_CO_22_project_is_current_directory_without_env(self):
        self.build(cfg={"version": 1, "bogus": True})
        proc = self.cli("--check-config", with_project=False, cwd=self.project)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(any("bogus" in line for line in self.lines(proc)))

    def test_CO_22_writes_nothing_to_state_directory(self):
        fresh = os.path.join(self.tmp, "fresh-state")
        for _ in range(2):
            proc = self.cli("--check-config", cfg=OK_CONFIG, state_home=fresh)
            self.assertEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(fresh))

    def test_CO_23_rejected_configs_name_the_problem(self):
        good_stop = stop_cfg()
        no_run = {k: v for k, v in good_stop.items() if k != "run"}
        rows = (
            (None, []),
            ("{ nope", ["json"]),
            ("[1]", []),
            ("{}", ["version"]),
            ('{"version": 2}', ["version"]),
            ('{"version": "1"}', ["version"]),
            ({"version": 1, "bogus": 1}, ["bogus"]),
            ({"version": 1, "roles": {"tester": [], "boss": []}}, ["roles.boss"]),
            ({"version": 1, "hidden": {"foo": []}}, ["hidden.foo"]),
            ({"version": 1, "tests": {"nope": 1}}, ["tests.nope"]),
            ({"version": 1, "notes": {"x": "a"}}, ["notes.x"]),
            ({"version": 1, "reports": {"x": "a"}}, ["reports.x"]),
            ({"version": 1, "visual": {"x": 1}}, ["visual.x"]),
            ({"version": 1, "modes": {"default": "turbo"}}, ["modes.default"]),
            ({"version": 1, "modes": {"extra": 1}}, ["modes.extra"]),
            ({"version": 1, "roles": "x"}, ["roles"]),
            ({"version": 1, "hidden": []}, ["hidden"]),
            ({"version": 1, "stop": stop_cfg(timeout=1801)}, ["timeout"]),
            ({"version": 1, "stop": stop_cfg(timeout=0)}, ["timeout"]),
            ({"version": 1, "stop": stop_cfg(timeout="300")}, ["timeout"]),
            ({"version": 1, "stop": stop_cfg(timeout=-5)}, ["timeout"]),
            ({"version": 1, "stop": stop_cfg(bogus=1)}, ["stop.bogus"]),
            ({"version": 1, "stop": {"run": "true"}},
             ["stop.dirty_pathspec", "stop.fresh_paths", "stop.fresh_globs", "stop.exclude_dirs"]),
            ({"version": 1, "stop": no_run}, ["stop.run"]),
        )
        for cfg, needles in rows:
            with self.subTest(config=cfg):
                self.setUp()
                if cfg is None:
                    proc = self.cli("--check-config")
                elif isinstance(cfg, str):
                    proc = self.cli("--check-config", raw=cfg)
                else:
                    proc = self.cli("--check-config", cfg=cfg)
                self.assertNotEqual(proc.returncode, 0)
                lines = self.lines(proc)
                self.assertTrue(lines)
                self.assertTrue(all(line.startswith("specguard: ") for line in lines))
                for needle in needles:
                    self.assertTrue(any(needle in line.lower() or needle in line for line in lines),
                                    "no line names {}: {}".format(needle, lines))

    def test_CO_23_accepted_edge_values(self):
        rows = (
            {"version": 1, "modes": {"default": "visual"}},
            {"version": 1, "modes": {"default": "hard"}},
            {"version": 1, "stop": stop_cfg(timeout=1800)},
        )
        for cfg in rows:
            with self.subTest(config=cfg):
                proc = self.cli("--check-config", cfg=cfg)
                self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "OK"))

    def test_CO_23_every_problem_is_listed(self):
        proc = self.cli("--check-config",
                        cfg={"version": 3, "bogus": 1, "modes": {"default": "turbo"}})
        lines = self.lines(proc)
        self.assertNotEqual(proc.returncode, 0)
        self.assertGreaterEqual(len(lines), 3)
        first = {}
        for needle in ("version", "bogus", "modes.default"):
            first[needle] = next((i for i, line in enumerate(lines) if needle in line), None)
            self.assertIsNotNone(first[needle], needle)
        self.assertEqual(len(set(first.values())), 3)


class StatusTests(Base):
    def status(self, cfg=OK_CONFIG, raw=None, plugin_root=None):
        proc = self.cli("--status", cfg=cfg if raw is None else None, raw=raw, plugin_root=plugin_root)
        self.assertEqual(proc.returncode, 0)
        return proc.stdout.splitlines()

    def test_CO_24_empty_state_summary(self):
        lines = self.status()
        self.assertEqual(lines[:6], [
            "specguard: status for {}".format(self.project),
            "no sessions recorded yet",
            "no approved specs recorded",
            "last green: never",
            "log: empty",
            "config: OK",
        ])
        self.assertEqual(len(lines), 7)
        self.assertTrue(lines[6].startswith("tree hash: "))
        self.assertRegex(lines[6][len("tree hash: "):], HEX64)
        self.assertEqual(os.listdir(self.state_home), [])

    def test_CO_24_session_lines(self):
        rows = (
            ({"modes": ["feature"], "approval": True,
              "requests": {"docs/specs/b.md": {}, "docs/specs/a.md": {}}},
             "session s1 - modes feature - approval on - open requests docs/specs/a.md, docs/specs/b.md"),
            ({"modes": ["visual", "feature"], "approval": False, "requests": {}},
             "session s1 - modes visual+feature - approval off - open requests none"),
        )
        for sess, expected in rows:
            with self.subTest(expected=expected):
                self.setUp()
                self.build(cfg=OK_CONFIG)
                self.session("s1", sess)
                lines = self.status()
                self.assertIn(expected, lines)
                self.assertNotIn("no sessions recorded yet", lines)

    def test_CO_24_sessions_sorted_by_id(self):
        self.build(cfg=OK_CONFIG)
        self.session("s2", {"modes": ["simple"], "approval": True, "requests": {}})
        self.session("s1", {"modes": ["simple"], "approval": True, "requests": {}})
        lines = self.status()
        ids = [line.split()[1] for line in lines if line.startswith("session ")]
        self.assertEqual(ids, ["s1", "s2"])

    def test_CO_24_approved_lines_sorted_by_spec(self):
        self.build(cfg=OK_CONFIG)
        full = "3f2a91c" + "0" * 57
        self.write_state("approved-specs.json", {"docs/specs/z.md": {"sha256": full},
                                                 "docs/specs/a.md": {"sha256": "abcdef0123456789"}})
        lines = self.status()
        self.assertEqual([l for l in lines if l.startswith("approved ")],
                         ["approved docs/specs/a.md (abcdef0)", "approved docs/specs/z.md (3f2a91c)"])
        self.assertNotIn("no approved specs recorded", lines)

    def test_CO_24_last_green_uses_local_time_of_file(self):
        self.build(cfg=OK_CONFIG)
        path = self.write_state("last-green", "")
        stamp = 1700000000
        os.utime(path, (stamp, stamp))
        expected = "last green: " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))
        lines = self.status()
        self.assertIn(expected, lines)
        self.assertNotIn("last green: never", lines)

    def test_CO_24_empty_last_green_shows_a_date(self):
        self.build(cfg=OK_CONFIG)
        self.write_state("last-green", "")
        line = next(l for l in self.status() if l.startswith("last green: "))
        self.assertRegex(line, r"^last green: \d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")

    def test_CO_24_log_shows_last_five_lines(self):
        for count, expected in ((8, range(4, 9)), (2, range(1, 3))):
            with self.subTest(count=count):
                self.setUp()
                self.build(cfg=OK_CONFIG)
                written = [json.dumps({"t": 1, "ev": "n{}".format(i)}) for i in range(1, count + 1)]
                self.write_state("log.jsonl", "\n".join(written) + "\n")
                lines = self.status()
                self.assertEqual([l for l in lines if l.startswith("log: ")],
                                 ["log: " + written[i - 1] for i in expected])

    def test_CO_24_config_problems_are_reported(self):
        rows = (
            ("{ nope", None, ""),
            (None, None, ""),
            ('{"version": 1, "bogus": 1}', None, "bogus"),
        )
        for raw, _, needle in rows:
            with self.subTest(config=raw):
                self.setUp()
                lines = self.status(cfg=None, raw=raw)
                problems = [l for l in lines if l.startswith("config problem: ")]
                self.assertTrue(problems)
                self.assertTrue(any(needle in p for p in problems))
                self.assertNotIn("config: OK", lines)

    def test_CO_24_status_creates_nothing_in_prepared_state(self):
        self.build(cfg=OK_CONFIG)
        self.session("s1", {"modes": ["feature"]})
        before = walk(self.state_home)
        self.status()
        self.assertEqual(walk(self.state_home), before)

    def test_CO_25_session_defaults_in_status(self):
        feature = {"version": 1, "modes": {"default": "feature"}}
        no_approval = {"version": 1, "modes": {"approval_default": False}}
        rows = (
            (OK_CONFIG, {"modes": ["feature"]}, "session s1 - modes feature - approval on - open requests none"),
            (no_approval, {"modes": ["feature"]}, "session s1 - modes feature - approval off - open requests none"),
            (feature, {}, "session s1 - modes feature - approval on - open requests none"),
            (OK_CONFIG, {}, "session s1 - modes simple - approval on - open requests none"),
            (OK_CONFIG, {"modes": ["hard"], "approval": False},
             "session s1 - modes hard - approval on - open requests none"),
        )
        for cfg, sess, expected in rows:
            with self.subTest(session=sess, cfg=cfg):
                self.setUp()
                self.build(cfg=cfg)
                self.session("s1", sess)
                self.assertIn(expected, self.status(cfg=cfg))


class TreeHashTests(Base):
    def tree_hash(self, files, name="plugin"):
        root = os.path.join(self.tmp, name)
        os.makedirs(root, exist_ok=True)
        for rel, content in files.items():
            full = os.path.join(root, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(content.encode("utf-8"))
        proc = self.cli("--status", cfg=OK_CONFIG, plugin_root=root)
        self.assertEqual(proc.returncode, 0)
        line = proc.stdout.splitlines()[-1]
        self.assertTrue(line.startswith("tree hash: "))
        return line[len("tree hash: "):]

    def test_CO_26_empty_tree(self):
        self.assertEqual(self.tree_hash({}),
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_CO_26_hash_follows_path_order_and_format(self):
        rows = (
            {"scripts/a.py": "x = 1\n"},
            {"scripts/b.py": "b", "agents/a.md": "a"},
            {"scripts/pkg/z.py": "z", "scripts/a.py": "a"},
            {".claude-plugin/plugin.json": "{}", "hooks/hooks.json": "h", "scripts/a.py": "s",
             "agents/t.md": "t", "skills/s/SKILL.md": "k", "phrases/p.json": "p"},
        )
        for i, files in enumerate(rows):
            with self.subTest(files=sorted(files)):
                self.assertEqual(self.tree_hash(files, name="p{}".format(i)), digest(files))

    def test_CO_26_bytecode_and_outside_files_are_ignored(self):
        base = {"scripts/a.py": "x = 1\n"}
        extra = dict(base, **{"scripts/__pycache__/a.cpython-312.pyc": "c", "scripts/junk.pyc": "j"})
        outside = dict(base, **{"README.md": "r", "tests/t.py": "t", "docs/x.md": "d"})
        self.assertEqual(self.tree_hash(extra, name="p1"), digest(base))
        self.assertEqual(self.tree_hash(outside, name="p2"), digest(base))

    def test_CO_26_content_and_name_change_the_hash(self):
        original = self.tree_hash({"scripts/a.py": "x = 1\n"}, name="p1")
        self.assertNotEqual(self.tree_hash({"scripts/a.py": "x = 2\n"}, name="p2"), original)
        self.assertNotEqual(self.tree_hash({"scripts/c.py": "x = 1\n"}, name="p3"), original)

    def test_CO_26_same_tree_at_two_roots_hashes_equal(self):
        files = {"scripts/a.py": "x = 1\n"}
        self.assertEqual(self.tree_hash(files, name="p1"), self.tree_hash(files, name="p2"))


if __name__ == "__main__":
    unittest.main()
