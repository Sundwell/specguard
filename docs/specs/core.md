# Core - hook entrypoint, fail-safe, config, state files, --status and --check-config

Tests: tests/test_spec_core.py

The core is the layer every specguard hook call passes through. It reads one hook event from stdin, decides whether the project is under specguard at all, loads `.claude/specguard.json`, routes the event, prints at most one JSON object and always exits 0. When something inside breaks it fails safe - closed for the tester and advocate roles, open with one notice for everyone else. It also owns the state files under the state directory and the two read-only commands `--status` and `--check-config`.

## Public API

Everything is driven from the outside, one process per call. Nothing from `scripts/` is imported.

Import path - `tests/helpers.py`, imported as `import helpers` (the test file sits next to it).

- `helpers.make_project(tmp, config=None, files=None, git=False, commit=False)` builds a project directory. `config` is a dict written to `.claude/specguard.json`. To write a broken config, write the file yourself after `make_project(tmp)`.
- `helpers.run_hook(event, payload, project_dir, env=None, state_home=None)` runs the hook once and returns `(exit_code, stdout_json, stderr)`. `stdout_json` is `None` when stdout is empty. It sets `CLAUDE_PROJECT_DIR` to `project_dir` and `XDG_STATE_HOME` to `state_home`, or to a fresh temp directory when `state_home` is not given; pass the same `state_home` to several calls to make them share state. `env` overrides or adds environment variables. `plugin_root` is also accepted.
- Payload builders - `helpers.pre_tool_use(session_id, tool_name, tool_input, **extra)`, `helpers.user_prompt_submit(session_id, prompt, **extra)`, `helpers.session_start(session_id)`, `helpers.subagent_start(session_id, agent_id, agent_type)`, `helpers.stop(session_id)`, `helpers.post_tool_use(...)`, `helpers.user_prompt_expansion(...)`. `extra` accepts `agent_type` and `agent_id`. To send something the builders cannot make (garbage stdin, an unknown event name), call `subprocess.run([sys.executable, "-B", helpers.HOOK_PATH], input=..., env=...)` yourself with the same environment `run_hook` builds.
- `helpers.HOOK_PATH` is the entrypoint script. The two commands are run as `subprocess.run([sys.executable, "-B", helpers.HOOK_PATH, "--status"])` and the same with `"--check-config"`, with `CLAUDE_PROJECT_DIR` set to the project, `XDG_STATE_HOME` set, and `CLAUDE_PLUGIN_ROOT` set to a plugin tree of the test's choosing where the rule needs one. They print to stdout and their exit code is the process exit code.

State directory - `<XDG_STATE_HOME>/specguard/<key>/`, where `<key>` is the absolute project path with every `/` replaced by `-` (project `/tmp/p1` gives key `-tmp-p1`). When `XDG_STATE_HOME` is unset or empty it is `~/.local/state`. Files the tests may read or prepare inside it.

- `sessions/<session_id>.json` - JSON object with `modes` (list of mode names), `approval` (boolean), `requests` (object, spec path to any object).
- `agents.json` - JSON object, `agent_id` to `{"session_id": ..., "agent_type": ...}`.
- `approved-specs.json` - JSON object, spec path to an object with `sha256` (hex string).
- `last-green` - an empty file whose modification time is the last green run.
- `log.jsonl` - one JSON object per line, each with the keys `t` and `ev`.
- `errors.log` - text, appended to on every internal failure.
- `<name>.lock` next to a shared file such as `agents.json` - the lock file for that file. A test may hold `fcntl.flock(fd, LOCK_EX)` on it to simulate another process.

Observable outputs used below.

- A refusal is `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "<text>"}}`.
- A notice is `{"systemMessage": "<text>"}`.
- Context for the model is `{"hookSpecificOutput": {"hookEventName": "<event>", "additionalContext": "<text>"}}`.
- One hook call prints one JSON object with any of these parts combined, or nothing.

Roles - an `agent_type` in `roles.tester` (default `["specguard:tester", "tester"]`) is the tester role, one in `roles.advocate` (default `["specguard:devils-advocate", "devils-advocate"]`) is the advocate role, anything else or no `agent_type` is the executor.

## Rules

- CO-1 A project with no `.claude/specguard.json` gets nothing from any event: exit 0, empty stdout, empty stderr, and no file or directory is created in the state directory. This is why the plugin can be installed once for a whole user account.
- CO-2 The project is `CLAUDE_PROJECT_DIR` when it is set and non-empty, otherwise the `cwd` of the payload. The config is looked up in that project only. This is why a session started in a subfolder still finds the root config.
- CO-3 A hook call always exits 0 and never writes to stderr, whatever stdin holds - valid event, unknown event name, empty stdin, text that is not JSON, JSON that is not an object. Anything else would show the user a hook failure or block the tool call.
- CO-4 An event name the core does not know, and stdin that carries no event name, produce empty stdout when the project has a valid config. Only the seven events PreToolUse, PostToolUse, UserPromptSubmit, UserPromptExpansion, SessionStart, SubagentStart and Stop are routed.
- CO-5 A call the guard allows prints nothing and never a `permissionDecision` of `allow`. An `allow` would skip the user's own permission prompts.
- CO-6 With a config that is not usable - the file is not valid JSON, its top level is not an object, or a known section has the wrong type (for example `"hidden": "oops"`) - a PreToolUse call from a role is refused with a `permissionDecisionReason` that contains `guard error`, on every call, and with no `systemMessage`. This is the fail-closed half of the fail-safe.
- CO-7 The same broken config and a PreToolUse call from the executor produce no `permissionDecision` at all. The first such call in a session prints `{"systemMessage": "specguard: hook error, checks off, see errors.log"}` and exits 0; every later call of the same `session_id` prints nothing, whatever the event; a different `session_id` gets its own single notice. This is the fail-open half - the executor is not blocked, but told once.
- CO-8 When the config file cannot be read as JSON, a caller counts as a role when its `agent_type` is `tester` or `devils-advocate` with an optional prefix ending in `:` (`specguard:tester`, `x:devils-advocate`); `testers`, `mytester`, `executor` and `tester2` do not count. When the config is readable JSON with a `roles` object, the role lists of that object decide instead, each missing list falling back to its default, so `roles.tester = ["qa-writer"]` makes `qa-writer` a role and `tester` a plain executor.
- CO-9 Events other than PreToolUse never produce a `permissionDecision` on failure, for any caller. They produce the once-per-session notice of CO-7 and nothing else. The once-per-session counter is shared by all events of a session.
- CO-10 While the session file says `hard` is among the modes, a PreToolUse `Agent` launch by the executor whose `subagent_type` is `tester` or `specguard:tester` is refused on failure, with a reason that contains `hard mode`, on every call. In any other mode, for any other `subagent_type`, or for any other tool, the executor gets the CO-7 behaviour. This is why hard mode does not silently lose its gates.
- CO-11 Every internal failure appends a non-empty entry to `errors.log` in the state directory. A call that fails does not touch `log.jsonl`.
- CO-12 When a session has no session file, its modes are `[modes.default]` and its approval flag is `modes.approval_default`; `modes.default` defaults to `simple`, `modes.approval_default` to `true`. The user sees this in the line every executor UserPromptSubmit carries in `additionalContext`, `Active specguard mode is <modes joined by +>, approval on.` or `off`; the approval part appears only when the modes include `feature` or `hard`, so a `simple` or `visual` session reads `Active specguard mode is simple.`.
- CO-13 A session file replaces the defaults for its own `session_id` only. `modes` and `approval` in it are what the line of CO-12 reports; sessions with different ids do not affect each other.
- CO-14 In a session whose modes include `hard`, approval is on whatever the session file says. The line reads `approval on`.
- CO-15 A role's UserPromptSubmit carries no status line. The role is decided by CO-8's `roles` lists, so with `roles.tester = ["qa-writer"]` the agent type `qa-writer` gets no line and the agent type `tester` gets one.
- CO-16 SubagentStart records the subagent in `agents.json` as `agent_id` to `{"session_id": <the payload's session_id>, "agent_type": <the payload's agent_type>}`. A second SubagentStart adds its entry and keeps the first; a repeat of the same `agent_id` replaces its entry.
- CO-17 Concurrent hook processes never lose a record. Eight SubagentStart processes started at the same time with eight different `agent_id` values leave all eight in `agents.json`.
- CO-18 A state lock held by another process is waited for at most 2 seconds. After that the operation gives up and the call takes the fail-safe path (CO-7, so the executor's first call prints the notice), instead of hanging until the hook is killed. The call returns in under 6 seconds and the entry is not recorded.
- CO-19 State lives only in the state directory. No file or directory is created inside the project by a hook call (the project keeps exactly the files the test built), and no `__pycache__` or `.pyc` appears under the plugin root.
- CO-20 A prompt event prints one combined object. When a prompt switches the mode, the `systemMessage` announces the switch and the `additionalContext` status line already shows the new state.
- CO-21 SessionStart of the executor prints context for the model whose `hookEventName` is `SessionStart` and whose text names the `specguard:guide` skill. This is the routing check that SessionStart reaches its own handler.
- CO-22 `--check-config` validates the config of the project (the `CLAUDE_PROJECT_DIR` directory, else the current directory) without changing anything. A valid config prints exactly `OK` and exits 0. Otherwise it prints one line per problem, each starting `specguard: `, and exits non-zero. It writes nothing to the state directory.
- CO-23 What `--check-config` rejects - a missing file, invalid JSON, a top level that is not an object, a missing `version`, a `version` other than `1`, an unknown key at the top level or inside `roles`, `hidden`, `tests`, `notes`, `reports`, `modes`, `stop` or `visual` (the line names the key), a section that is not an object, a `modes.default` other than `simple`, `feature`, `hard` or `visual`, a `stop.timeout` that is not a positive number or is above 1800, and a `stop` object missing any of `dirty_pathspec`, `fresh_paths`, `fresh_globs`, `exclude_dirs`, `run` (the line names the missing key). Every problem of a config is listed, not only the first. The known keys are - top level `version`, `repo`, `roles`, `hidden`, `tester_readable`, `tests`, `docs_dirs`, `specs_dir`, `notes`, `reports`, `plan_file`, `money_topics`, `hands_on_tag`, `modes`, `stop`, `api_report`, `visual`; `roles` and `notes` `tester`, `advocate`; `hidden` `segments`, `names`, `paths`; `tests` `segments`, `paths`, `file_regex`; `reports` `tester`, `advocate`, `advocate_hidden`; `modes` `default`, `approval_default`; `stop` `modes`, `dirty_pathspec`, `fresh_paths`, `fresh_globs`, `exclude_dirs`, `skip_preset`, `skip_regex`, `skip_paths`, `run`, `summary_regex`, `timeout`, `require_glob`; `visual` `ui_paths`, `deviations_log`, `notes`. A config using only these keys, with values of the right shape, is not rejected.
- CO-24 `--status` prints a read-only summary of the project and exits 0, also when the config has problems or there is no config. Its lines, in this order, are `specguard: status for <project dir>`; one `session <id> - modes <modes joined by +> - approval on|off - open requests <request specs joined by ", ", or none>` line per file in `sessions/` sorted by id, or `no sessions recorded yet`; one `approved <spec> (<first 7 characters of sha256>)` line per entry of `approved-specs.json` sorted by spec, or `no approved specs recorded`; `last green: never` or `last green: <YYYY-MM-DD HH:MM:SS local time of last-green>`; up to the last 5 lines of `log.jsonl`, each as `log: <line>`, or `log: empty`; `config: OK` or one `config problem: <problem>` line per problem of CO-23; `tree hash: <64 hex characters>`. It creates nothing in the state directory.
- CO-25 A session file without `approval` or without `modes` is shown by `--status` with the defaults of CO-12 (`on` unless `modes.approval_default` is `false`; the mode `modes.default`), and a session whose modes include `hard` is shown as `approval on`.
- CO-26 The tree hash of `--status` is the sha256 hex digest over every file under the directories `.claude-plugin`, `hooks`, `scripts`, `agents`, `skills` and `phrases` of `CLAUDE_PLUGIN_ROOT`, taken in the order of their `/`-separated paths relative to the root (string order of the whole path), each contributing its relative path in UTF-8, one NUL byte, its content and one NUL byte. Files in a `__pycache__` folder and files ending `.pyc` are skipped, and so is everything outside those six directories. A directory that does not exist is skipped. This is the value a pinned copy is compared by.

## Examples

CO-1, CO-3, CO-4 - `run_hook` or a raw process, no config for CO-1 rows, a config `{"version": 1}` for the others.

| Rule | Event and stdin | Expected |
|---|---|---|
| CO-1 | PreToolUse Read `a.py`, no config | exit 0, stdout empty, stderr empty, state directory not created |
| CO-1 | UserPromptSubmit `go feature spec`, no config | exit 0, stdout empty, state directory not created |
| CO-1 | SubagentStart `a1` `specguard:tester`, no config | exit 0, stdout empty, no `agents.json` |
| CO-3 | stdin empty, config `{"version": 1}` | exit 0, stdout empty, stderr empty |
| CO-3 | stdin `not json {`, config `{"version": 1}` | exit 0, stdout empty, stderr empty |
| CO-3 | stdin `[1, 2]`, config `{"version": 1}` | exit 0, stdout empty, stderr empty |
| CO-3 | stdin `"a string"`, no config | exit 0, stdout empty |
| CO-4 | `{"hook_event_name": "Elicitation", "session_id": "s1"}`, config `{"version": 1}` | exit 0, stdout empty |
| CO-4 | `{"session_id": "s1"}` (no event name), config `{"version": 1}` | exit 0, stdout empty |

CO-2 - the hook process gets `CLAUDE_PROJECT_DIR` and payload `cwd`, each a directory.

| Rule | CLAUDE_PROJECT_DIR | payload cwd | Caller | Expected |
|---|---|---|---|---|
| CO-2 | dir A, broken config (`{`) | dir B, no config | tester PreToolUse Read | refused with `guard error` |
| CO-2 | dir A, no config | dir B, broken config (`{`) | tester PreToolUse Read | empty stdout |
| CO-2 | unset in the environment, payload cwd is dir B with broken config | dir B | tester PreToolUse Read | refused with `guard error` |

CO-5 - config `{"version": 1}`, session `s1`.

| Rule | Call | Expected |
|---|---|---|
| CO-5 | executor PreToolUse Read `notes.txt` | empty stdout |
| CO-5 | executor PreToolUse Bash `ls` | empty stdout |

CO-6 to CO-11 - the config variants.

| Name | Config file content |
|---|---|
| broken-json | `{ this is not json` |
| not-object | `[1, 2, 3]` |
| wrong-type | `{"version": 1, "hidden": "oops"}` |

| Rule | Config | Call | Expected |
|---|---|---|---|
| CO-6 | broken-json | PreToolUse Read by `specguard:tester` | deny, reason contains `guard error`, no `systemMessage` |
| CO-6 | broken-json | PreToolUse Read by `tester`, three times in a row | deny all three times |
| CO-6 | broken-json | PreToolUse Read by `specguard:devils-advocate` | deny |
| CO-6 | not-object | PreToolUse Grep by `tester` | deny |
| CO-6 | wrong-type | PreToolUse Read by `specguard:tester` | deny |
| CO-7 | broken-json | PreToolUse Read by the executor, first call of `s1` | `{"systemMessage": "specguard: hook error, checks off, see errors.log"}`, exit 0, no `hookSpecificOutput` |
| CO-7 | broken-json | second PreToolUse of `s1` after the first | empty stdout |
| CO-7 | broken-json | first PreToolUse of `s2` after `s1` was notified | the same `systemMessage` |
| CO-7 | not-object | PreToolUse Bash by the executor, first call | the `systemMessage`, no `permissionDecision` |
| CO-7 | wrong-type | PreToolUse Read by the executor, first call | the `systemMessage` |
| CO-8 | broken-json | agent_type `tester`, PreToolUse | deny |
| CO-8 | broken-json | agent_type `foo:tester` | deny |
| CO-8 | broken-json | agent_type `x:devils-advocate` | deny |
| CO-8 | broken-json | agent_type `testers` | notice, no deny |
| CO-8 | broken-json | agent_type `mytester` | notice, no deny |
| CO-8 | broken-json | agent_type `tester2` | notice, no deny |
| CO-8 | broken-json | agent_type `executor` | notice, no deny |
| CO-8 | `{"version": 1, "roles": {"tester": ["qa-writer"]}, "hidden": "oops"}` | agent_type `qa-writer` | deny |
| CO-8 | `{"version": 1, "roles": {"tester": ["qa-writer"]}, "hidden": "oops"}` | agent_type `tester` | notice, no deny |
| CO-8 | `{"version": 1, "roles": {"tester": ["qa-writer"]}, "hidden": "oops"}` | agent_type `specguard:devils-advocate` (advocate list falls back to default) | deny |
| CO-9 | broken-json | UserPromptSubmit by the executor, first call of `s1` | the `systemMessage`, no `hookSpecificOutput` |
| CO-9 | broken-json | UserPromptSubmit by `tester`, first call of `s1` | the `systemMessage`, no `permissionDecision` |
| CO-9 | broken-json | Stop of `s1` after a notified PreToolUse of `s1` | empty stdout |
| CO-9 | broken-json | PreToolUse of `s1` after a notified SessionStart of `s1` | empty stdout |
| CO-11 | broken-json | one failing call | `errors.log` exists, is non-empty; no `log.jsonl` |
| CO-11 | broken-json | two failing calls | `errors.log` holds more text after the second than after the first |

CO-10 - config broken-json, the session file `sessions/s1.json` prepared in the state directory.

| Rule | Session file | Call (executor unless said) | Expected |
|---|---|---|---|
| CO-10 | `{"modes": ["hard"]}` | PreToolUse Agent `subagent_type` `specguard:tester` | deny, reason contains `hard mode` |
| CO-10 | `{"modes": ["hard"]}` | PreToolUse Agent `subagent_type` `tester`, twice | deny both times |
| CO-10 | `{"modes": ["visual", "hard"]}` | PreToolUse Agent `subagent_type` `tester` | deny |
| CO-10 | `{"modes": ["hard"]}` | PreToolUse Agent `subagent_type` `Explore` | the `systemMessage`, no deny |
| CO-10 | `{"modes": ["hard"]}` | PreToolUse Read | the `systemMessage`, no deny |
| CO-10 | `{"modes": ["feature"]}` | PreToolUse Agent `subagent_type` `tester` | the `systemMessage`, no deny |
| CO-10 | none | PreToolUse Agent `subagent_type` `tester` | the `systemMessage`, no deny |
| CO-10 | `{"modes": ["hard"]}` | UserPromptSubmit by the executor | the `systemMessage`, no deny |

CO-12 to CO-15 - a UserPromptSubmit with prompt `please continue` by the executor, session `s1`; expected is the `additionalContext` text.

| Rule | Config | Session file | Expected additionalContext |
|---|---|---|---|
| CO-12 | `{"version": 1}` | none | `Active specguard mode is simple.` |
| CO-12 | `{"version": 1, "modes": {"default": "feature"}}` | none | `Active specguard mode is feature, approval on.` |
| CO-12 | `{"version": 1, "modes": {"default": "feature", "approval_default": false}}` | none | `Active specguard mode is feature, approval off.` |
| CO-12 | `{"version": 1, "modes": {"default": "hard", "approval_default": false}}` | none | `Active specguard mode is hard, approval on.` |
| CO-12 | `{"version": 1, "modes": {"default": "visual"}}` | none | `Active specguard mode is visual.` |
| CO-13 | `{"version": 1}` | `{"modes": ["feature"], "approval": false}` | `Active specguard mode is feature, approval off.` |
| CO-13 | `{"version": 1, "modes": {"default": "feature"}}` | `{"modes": ["visual", "feature"], "approval": true}` | `Active specguard mode is visual+feature, approval on.` |
| CO-13 | `{"version": 1, "modes": {"default": "feature"}}` | `sessions/other.json` is `{"modes": ["hard"]}`, session `s1` has no file | `Active specguard mode is feature, approval on.` |
| CO-13 | `{"version": 1, "modes": {"default": "feature"}}` | `s1` is `{"modes": ["simple"]}` | `Active specguard mode is simple.` |
| CO-14 | `{"version": 1}` | `{"modes": ["hard"], "approval": false}` | `Active specguard mode is hard, approval on.` |
| CO-14 | `{"version": 1}` | `{"modes": ["visual", "hard"], "approval": false}` | `Active specguard mode is visual+hard, approval on.` |
| CO-15 | `{"version": 1, "modes": {"default": "feature"}}` | none, agent_type `specguard:tester` | no `additionalContext` |
| CO-15 | `{"version": 1, "modes": {"default": "feature"}}` | none, agent_type `specguard:devils-advocate` | no `additionalContext` |
| CO-15 | `{"version": 1, "modes": {"default": "feature"}, "roles": {"tester": ["qa-writer"]}}` | none, agent_type `qa-writer` | no `additionalContext` |
| CO-15 | `{"version": 1, "modes": {"default": "feature"}, "roles": {"tester": ["qa-writer"]}}` | none, agent_type `tester` | `Active specguard mode is feature, approval on.` |

CO-16 to CO-19 - config `{"version": 1}`.

| Rule | Calls | Expected in `agents.json` |
|---|---|---|
| CO-16 | SubagentStart session `s1`, agent `a1`, type `specguard:tester` | `{"a1": {"session_id": "s1", "agent_type": "specguard:tester"}}` |
| CO-16 | then SubagentStart session `s1`, agent `a2`, type `Explore`, same state directory | both `a1` and `a2`, `a2` has type `Explore` |
| CO-16 | then SubagentStart session `s2`, agent `a1`, type `Explore` | `a1` is `{"session_id": "s2", "agent_type": "Explore"}`, `a2` still there |
| CO-17 | eight SubagentStart processes at once, agents `a0` to `a7`, one shared state directory | all eight keys present, file is valid JSON |
| CO-18 | test holds an exclusive lock on `agents.json.lock`, SubagentStart agent `a1` runs, lock released after the call returns | call returns in under 6 seconds, exit 0, `agents.json` has no `a1` |
| CO-18 | same, no lock held, SubagentStart agent `a1` | `a1` recorded |
| CO-19 | one PreToolUse, one UserPromptSubmit, one SubagentStart | the project directory holds only the files the test built |
| CO-19 | the hook run on a copy of the plugin tree (`scripts`, `hooks`) in a temp directory | no `__pycache__` directory and no `.pyc` file under the copy |
| CO-19 | `XDG_STATE_HOME` set to an empty string and `HOME` set to a temp directory, one SubagentStart | `agents.json` exists at `$HOME/.local/state/specguard/<key>/agents.json` |
| CO-19 | `XDG_STATE_HOME` set to `X`, one SubagentStart in project `/tmp/pq` (key `-tmp-pq`) | file at `X/specguard/-tmp-pq/agents.json` |

CO-20, CO-21 - config `{"version": 1}` unless said.

| Rule | Call | Expected |
|---|---|---|
| CO-20 | UserPromptSubmit `go feature spec` by the executor, session `s1` | `systemMessage` is `specguard: mode feature, approval on`; `additionalContext` contains `Active specguard mode is feature, approval on.` |
| CO-20 | UserPromptSubmit `go feature spec no approval` by the executor | `systemMessage` is `specguard: mode feature, approval off`; `additionalContext` contains `Active specguard mode is feature, approval off.` |
| CO-20 | UserPromptSubmit `go simple` with session `s1` already `{"modes": ["feature"]}` | `systemMessage` is `specguard: mode simple, approval on`; `additionalContext` contains `Active specguard mode is simple.` |
| CO-20 | UserPromptSubmit `please continue` by the executor | no `systemMessage`; `additionalContext` is the status line only |
| CO-21 | SessionStart of `s1`, executor | `hookSpecificOutput.hookEventName` is `SessionStart`, `additionalContext` contains `specguard:guide` |

CO-22, CO-23 - `--check-config`, configs written raw.

| Rule | Config file | Expected |
|---|---|---|
| CO-22 | `{"version": 1}` | stdout `OK`, exit 0 |
| CO-22 | full valid config with `stop` (all five keys, `timeout` 300), `roles`, `hidden`, `tests`, `notes`, `reports`, `modes`, `visual` | stdout `OK`, exit 0 |
| CO-22 | `{"version": 1, "bogus": true}` | exit non-zero, every stdout line starts `specguard: `, no `OK` line |
| CO-22 | valid config, run twice with the same fresh `XDG_STATE_HOME` | the state directory does not exist afterwards |
| CO-23 | no file | exit non-zero, a `specguard: ` line |
| CO-23 | `{ nope` | exit non-zero, a line that mentions JSON |
| CO-23 | `[1]` | exit non-zero |
| CO-23 | `{}` | exit non-zero, a line that names `version` |
| CO-23 | `{"version": 2}` | exit non-zero, a line that names `version` |
| CO-23 | `{"version": "1"}` | exit non-zero, a line that names `version` |
| CO-23 | `{"version": 1, "bogus": 1}` | a line that names `bogus` |
| CO-23 | `{"version": 1, "roles": {"tester": [], "boss": []}}` | a line that names `roles.boss` |
| CO-23 | `{"version": 1, "hidden": {"foo": []}}` | a line that names `hidden.foo` |
| CO-23 | `{"version": 1, "tests": {"nope": 1}}` | a line that names `tests.nope` |
| CO-23 | `{"version": 1, "notes": {"x": "a"}}` | a line that names `notes.x` |
| CO-23 | `{"version": 1, "reports": {"x": "a"}}` | a line that names `reports.x` |
| CO-23 | `{"version": 1, "visual": {"x": 1}}` | a line that names `visual.x` |
| CO-23 | `{"version": 1, "modes": {"default": "turbo"}}` | a line that names `modes.default` |
| CO-23 | `{"version": 1, "modes": {"extra": 1}}` | a line that names `modes.extra` |
| CO-23 | `{"version": 1, "roles": "x"}` | a line that names `roles` |
| CO-23 | `{"version": 1, "hidden": []}` | a line that names `hidden` |
| CO-23 | `{"version": 1, "modes": {"default": "visual"}}` | `OK` |
| CO-23 | `{"version": 1, "modes": {"default": "hard"}}` | `OK` |
| CO-23 | stop with the five keys and `timeout` 1800 | `OK` |
| CO-23 | stop with the five keys and `timeout` 1801 | a line that names `timeout` |
| CO-23 | stop with the five keys and `timeout` 0 | a line that names `timeout` |
| CO-23 | stop with the five keys and `timeout` `"300"` | a line that names `timeout` |
| CO-23 | stop with the five keys and `timeout` -5 | a line that names `timeout` |
| CO-23 | stop with the five keys and an extra `stop.bogus` | a line that names `stop.bogus` |
| CO-23 | `{"version": 1, "stop": {"run": "true"}}` | lines that name `stop.dirty_pathspec`, `stop.fresh_paths`, `stop.fresh_globs` and `stop.exclude_dirs` |
| CO-23 | stop with `dirty_pathspec`, `fresh_paths`, `fresh_globs`, `exclude_dirs` but no `run` | a line that names `stop.run` |
| CO-23 | `{"version": 3, "bogus": 1, "modes": {"default": "turbo"}}` | at least three problem lines, one each for `version`, `bogus`, `modes.default` |

CO-24 - `--status`. The state directory is prepared by writing files into it; `A` is a 64 character hex string starting `3f2a91c`.

| Rule | Prepared state | Expected |
|---|---|---|
| CO-24 | none, config `{"version": 1}` | exit 0; lines in order `specguard: status for <project dir>`, `no sessions recorded yet`, `no approved specs recorded`, `last green: never`, `log: empty`, `config: OK`, `tree hash: ` and 64 hex characters; the state directory is not created |
| CO-24 | `sessions/s1.json` `{"modes": ["feature"], "approval": true, "requests": {"docs/specs/b.md": {}, "docs/specs/a.md": {}}}` | line `session s1 - modes feature - approval on - open requests docs/specs/a.md, docs/specs/b.md` |
| CO-24 | `sessions/s1.json` `{"modes": ["visual", "feature"], "approval": false, "requests": {}}` | line `session s1 - modes visual+feature - approval off - open requests none` |
| CO-24 | `sessions/s2.json` and `sessions/s1.json` both present | the `s1` line before the `s2` line |
| CO-24 | `approved-specs.json` `{"docs/specs/z.md": {"sha256": A}, "docs/specs/a.md": {"sha256": "abcdef0123456789"}}` | lines `approved docs/specs/a.md (abcdef0)` then `approved docs/specs/z.md (3f2a91c)`, no `no approved specs recorded` line |
| CO-24 | an empty file `last-green` | `last green: ` followed by a date matching `\d{4}-\d\d-\d\d \d\d:\d\d:\d\d`, not `never` |
| CO-24 | `log.jsonl` with 8 lines `{"t": 1, "ev": "n<i>"}` for i 1 to 8 | exactly 5 `log: ` lines, holding the lines for `n4` to `n8` in order |
| CO-24 | `log.jsonl` with 2 lines | exactly 2 `log: ` lines |
| CO-24 | config `{ nope` | exit 0, a `config problem: ` line, no `config: OK` |
| CO-24 | no config file | exit 0, a `config problem: ` line |
| CO-24 | config `{"version": 1, "bogus": 1}` | `config problem: ` line that names `bogus`, no `config: OK` |
| CO-25 | `sessions/s1.json` `{"modes": ["feature"]}`, config `{"version": 1}` | `session s1 - modes feature - approval on - open requests none` |
| CO-25 | `sessions/s1.json` `{"modes": ["feature"]}`, config `modes.approval_default` false | `session s1 - modes feature - approval off - open requests none` |
| CO-25 | `sessions/s1.json` `{}`, config `modes.default` `feature` | `session s1 - modes feature - approval on - open requests none` |
| CO-25 | `sessions/s1.json` `{}`, config `{"version": 1}` | `session s1 - modes simple - approval on - open requests none` |
| CO-25 | `sessions/s1.json` `{"modes": ["hard"], "approval": false}` | `session s1 - modes hard - approval on - open requests none` |

CO-26 - `--status`, `CLAUDE_PLUGIN_ROOT` a temp tree; `sha(x)` is the digest defined by the rule.

| Rule | Plugin tree | Expected `tree hash` |
|---|---|---|
| CO-26 | empty directory | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| CO-26 | only `scripts/a.py` holding `x = 1\n` | sha256 of `scripts/a.py`, NUL, `x = 1\n`, NUL |
| CO-26 | `scripts/b.py` (`b`) and `agents/a.md` (`a`) | sha256 of `agents/a.md` NUL `a` NUL `scripts/b.py` NUL `b` NUL (sorted by path) |
| CO-26 | `scripts/pkg/z.py` (`z`) and `scripts/a.py` (`a`) | sha256 of `scripts/a.py` NUL `a` NUL `scripts/pkg/z.py` NUL `z` NUL |
| CO-26 | one file in each of the six directories | the hash of all six in path order |
| CO-26 | as the `scripts/a.py` row plus `scripts/__pycache__/a.cpython-312.pyc` and `scripts/junk.pyc` | the same hash as without them |
| CO-26 | as the `scripts/a.py` row plus `README.md` and `tests/t.py` and `docs/x.md` | the same hash as without them |
| CO-26 | as the `scripts/a.py` row with the content changed to `x = 2\n` | a different hash |
| CO-26 | the `scripts/a.py` row with the file renamed to `scripts/c.py` | a different hash |
| CO-26 | the same `scripts/a.py` tree copied to two different roots | the same hash from both |

## Retired rules

None.

## Recorded differences between the code as found and this spec

Kept here so the reader knows why the fixes below exist; the rules above are the intent.

- CO-14 and CO-25 - the status line and `--status` printed the raw stored approval flag, so a hard session with `approval: false` said `approval off` although hard forces approval on.
- CO-18 - the state lock fell back to an unbounded blocking wait after 2 seconds.
- CO-24 - the session line printed the approval flag as `True` or `False` and a missing flag as `None`; the spec fixes `on` and `off`, the words the rest of the plugin uses.
- CO-25 - a session file without `modes` was shown as `simple` even when the config default is another mode.
- CO-20 - the first draft of the `go simple` row expected `specguard: mode simple` without the approval part; the switch announcement always carries the approval word (only the status line of CO-12 omits it for `simple`), so the row was corrected to the code.
